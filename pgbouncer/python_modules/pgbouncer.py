import psycopg2
import functools
import time
import copy

_DSN = None
_DATABASES = None
_POOLS = None

_POOL_KEY = '%s_%s'

class Cache(object):
    '''
    cache for postgres query values
    prevents opening db connections for each metric_handler callback
    '''

    def __init__(self, expiry):
        self.expiry = expiry
        self.curr_time = 0
        self.last_time = 0
        self.last_value = None

    def __call__(self, func):
        @functools.wraps(func)
        def deco(*args, **kwds):
            self.curr_time = time.time()
            if self.curr_time - self.last_time > self.expiry:
                self.last_value = func(*args, **kwds)
                self.last_time = self.curr_time
            return self.last_value
        return deco

def get_cursor():
    conn = psycopg2.connect(_DSN)
    conn.autocommit = True
    return conn.cursor()

@Cache(30)
def get_metrics():
    '''
    update metrics dict with their values based on cache interval
    '''
    print "in get_metrics"

    metrics = {}

    cursor = get_cursor()

    cursor.execute('SHOW POOLS;')
    recordset = cursor.fetchall()

    for record in recordset:

        database, user, \
            cl_active, cl_waiting, \
            sv_active, sv_idle, sv_used, sv_tested, sv_login, \
            maxwait = record

        pool = _POOL_KEY % (database, user)

        # check that pool is in list of pools
        try:
            junk = _POOLS[pool]
        except KeyError:
            # not in list of pools
            continue

        metrics['pool_cl_active_%s' % pool] = cl_active
        metrics['pool_cl_waiting_%s' % pool] = cl_waiting

        metrics['pool_sv_active_%s' % pool] = sv_active
        metrics['pool_sv_idle_%s' % pool] = sv_idle
        metrics['pool_sv_used_%s' % pool] = sv_used
        metrics['pool_sv_tested_%s' % pool] = sv_tested
        metrics['pool_sv_login_%s' % pool] = sv_login

        metrics['pool_maxwait_%s' % pool] = maxwait

    cursor.close()
    return metrics

def metric_handler(name):
    '''
    metric handler uses dictionary keys based on metric name to return value
    '''

    metrics = get_metrics()
    return int(metrics[name])     

def _init_dsn(params):
    '''
    initialize the data source name
    '''

    global _DSN

    dsn = dict(
        host=None,
        port=None,
        user=None,
        password=None,
        sslmode=None
    )
    for key in dsn.keys():
        dsn[key] = params.get(key, None)

    # pgbouncer admin console is on the pgbouncer database
    dsn['dbname'] = 'pgbouncer'

    _DSN = ' '.join([
        key + '=' + val
        for key, val in dsn.iteritems()
        if val is not None
    ])

def _init_databases(params):

    global _DATABASES

    filtered_databases = params.get('databases', [])
    len_filtered = len(filtered_databases)

    cursor.execute('SHOW DATABASES;')
    recordset = cursor.fetchall()

    _DATABASES = []
    for record in recordset:

        name, host, port, database, force_user, pool_size = record

        if (
            len_filtered < 1 or
            database in filtered_databases
        ):
            _DATABASES.append(database)

    cursor.close()

def _init_pools(params):

    global _POOLS

    filtered_databases = params.get('databases', [])
    len_filtered = len(filtered_databases)

    cursor = get_cursor()

    cursor.execute('SHOW POOLS;')
    recordset = cursor.fetchall()

    _POOLS = {}
    for record in recordset:

        database, user, \
            cl_active, cl_waiting, \
            sv_active, sv_idle, sv_used, sv_tested, sv_login, \
            maxwait = record

        if (
            len_filtered < 1 or
            database in filtered_databases
        ):
            pool = _POOL_KEY % (database, user)
            _POOLS[pool] = {
                'database': database,
                'user': user
            }

    cursor.close()

def _init_descriptors():

    global _DESCRIPTORS

    def _create_descriptor(template, override):
        d = copy.deepcopy(template)
        for k, v in override.iteritems():
            d[k] = v
        return d

    _template = dict(
        name='XYZ',
        call_back=metric_handler,
        time_max=30,
        value_type='uint',
        format='%d',
        groups='pgBouncer'
    )

    _DESCRIPTORS = []
    for pool in _POOLS.keys():

        _DESCRIPTORS.append(_create_descriptor(
            _template, {
                'name': 'pool_cl_active_%s' % pool,
                'units': '# of connections',
                'description': 'count of currently active client connections'
            }
        ))

        _DESCRIPTORS.append(_create_descriptor(
            _template, {
                'name': 'pool_cl_waiting_%s' % pool,
                'units': '# of connections',
                'description': 'count of currently waiting client connections'
            }
        ))

        _DESCRIPTORS.append(_create_descriptor(
            _template, {
                'name': 'pool_sv_active_%s' % pool,
                'units': '# of connections',
                'description': 'count of currently active server connections'
            }
        ))

        _DESCRIPTORS.append(_create_descriptor(
            _template, {
                'name': 'pool_sv_idle_%s' % pool,
                'units': '# of connections',
                'description': 'count of currently idle server connections'
            }
        ))

        _DESCRIPTORS.append(_create_descriptor(
            _template, {
                'name': 'pool_sv_used_%s' % pool,
                'units': '# of connections',
                'description': 'count of currently used server connections'
            }
        ))

        _DESCRIPTORS.append(_create_descriptor(
            _template, {
                'name': 'pool_sv_tested_%s' % pool,
                'units': '# of connections',
                'description': 'count of currently tested server connections'
            }
        ))

        _DESCRIPTORS.append(_create_descriptor(
            _template, {
                'name': 'pool_sv_login_%s' % pool,
                'units': '# of connections',
                'description': 'count of server connections currently logged into PostgreSQL'
            }
        ))

        _DESCRIPTORS.append(_create_descriptor(
            _template, {
                'name': 'pool_maxwait_%s' % pool,
                'units': 'seconds',
                'description': 'how long the first (oldest) client in queue has waited, in seconds'
            }
        ))

    return _DESCRIPTORS

# Metric descriptors are initialized here 
def metric_init(params):

    # init dsn
    _init_dsn(params)

    # init databases
    #_init_databases(params)

    # init pools
    _init_pools(params)

    # init descriptors
    descriptors = _init_descriptors()

    return descriptors

# ganglia requires metric cleanup
def metric_cleanup():
    '''Clean up the metric module.'''
    pass

# this code is for debugging and unit testing    
if __name__ == '__main__':

    descriptors = metric_init({
        "host":"10.0.1.70",
        "port":"6432",
        "user":"alpine_webapp",
        "password":"501second",
        "sslmode": "disable",
        "databases": ""
    })

    while True:
        for d in descriptors:
            v = d['call_back'](d['name'])
            print 'value for %s is %u' % (d['name'],  v)
        time.sleep(5)

