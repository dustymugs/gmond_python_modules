import psycopg2
import functools
import time
import copy

_DSN = None
_DATABASES = None
_POOLS = None
_DBDELTAS = None
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

    global _DBDELTAS

    metrics = {}

    if _DBDELTAS is None:
        _DBDELTAS = {}
        new_deltas = True
    else:
        new_deltas = False

    cursor = get_cursor()

    #
    # db stats
    #
    cursor.execute('SHOW STATS;')
    recordset = cursor.fetchall()

    found_databases = []
    for record in recordset:

        database, \
            total_requests, total_received, total_sent, total_query_time, \
            avg_req, avg_recv, avg_sent, avg_query = record

        # check that database is in list of databases
        if database not in _DATABASES:
            continue

        found_databases.append(database)

        # deltas for totals
        if new_deltas:
            _DBDELTAS['stats_total_request_%s' % database] = total_requests
            _DBDELTAS['stats_total_received_%s' % database] = total_received
            _DBDELTAS['stats_total_sent_%s' % database] = total_sent
            _DBDELTAS['stats_total_query_time_%s' % database] = total_query_time
        
        metrics['stats_total_request_%s' % database] = \
            total_requests - _DBDELTAS['stats_total_request_%s' % database]
        metrics['stats_total_received_%s' % database] = \
            total_received - _DBDELTAS['stats_total_received_%s' % database]
        metrics['stats_total_sent_%s' % database] = \
            total_sent - _DBDELTAS['stats_total_sent_%s' % database]
        metrics['stats_total_query_time_%s' % database] = \
            total_query_time - _DBDELTAS['stats_total_query_time_%s' % database]

        _DBDELTAS['stats_total_request_%s' % database] = total_requests
        _DBDELTAS['stats_total_received_%s' % database] = total_received
        _DBDELTAS['stats_total_sent_%s' % database] = total_sent
        _DBDELTAS['stats_total_query_time_%s' % database] = total_query_time

        metrics['stats_avg_req_%s' % database] = avg_req
        metrics['stats_avg_recv_%s' % database] = avg_recv
        metrics['stats_avg_sent_%s' % database] = avg_sent
        metrics['stats_avg_query_%s' % database] = avg_query

    # fill in unfound databases
    for database in _DATABASES:
        if database in found_databases:
            continue

        _DBDELTAS['stats_total_request_%s' % database] = 0
        _DBDELTAS['stats_total_received_%s' % database] = 0
        _DBDELTAS['stats_total_sent_%s' % database] = 0
        _DBDELTAS['stats_total_query_time_%s' % database] = 0

        metrics['stats_total_request_%s' % database] = 0
        metrics['stats_total_received_%s' % database] = 0
        metrics['stats_total_sent_%s' % database] = 0
        metrics['stats_total_query_time_%s' % database] = 0

        metrics['stats_avg_req_%s' % database] = 0
        metrics['stats_avg_recv_%s' % database] = 0
        metrics['stats_avg_sent_%s' % database] = 0
        metrics['stats_avg_query_%s' % database] = 0

    #
    # pools
    #

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

    cursor = get_cursor()
    cursor.execute('SHOW DATABASES;')
    recordset = cursor.fetchall()

    _DATABASES = []
    for record in recordset:

        name, host, port, database, force_user, pool_size, reserve_pool = \
            record

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

def _create_descriptor(template, override):
    d = copy.deepcopy(template)
    for k, v in override.iteritems():
        d[k] = v
    return d

def _init_dbstats_descriptors(descriptors, template):

    template = copy.deepcopy(template)
    template['groups'] = 'pgBouncerStats'

    for db in _DATABASES:

        descriptors.append(_create_descriptor(
            template, {
                'name': 'stats_total_request_%s' % db,
                'units': '# of SQL requests',
                'description': 'total number of SQL requests pooled by pgbouncer since last access'
            }
        ))

        descriptors.append(_create_descriptor(
            template, {
                'name': 'stats_total_received_%s' % db,
                'units': 'bytes',
                'description': 'total volume in bytes of network traffic received by pgbouncer since last access'
            }
        ))

        descriptors.append(_create_descriptor(
            template, {
                'name': 'stats_total_sent_%s' % db,
                'units': 'bytes',
                'description': 'total volume in bytes of network traffic sent by pgbouncer since last access'
            }
        ))

        descriptors.append(_create_descriptor(
            template, {
                'name': 'stats_total_query_time_%s' % db,
                'units': 'microseconds',
                'description': 'total number of microseconds spent by pgbouncer when actively connected to PostgreSQL since last access'
            }
        ))

        descriptors.append(_create_descriptor(
            template, {
                'name': 'stats_avg_req_%s' % db,
                'units': '# of SQL requests',
                'description': 'average requests per second in last stat period'
            }
        ))

        descriptors.append(_create_descriptor(
            template, {
                'name': 'stats_avg_recv_%s' % db,
                'units': 'bytes/second',
                'description': 'average received (from clients) bytes per second'
            }
        ))

        descriptors.append(_create_descriptor(
            template, {
                'name': 'stats_avg_sent_%s' % db,
                'units': 'bytes/second',
                'description': 'average sent (to clients) bytes per second'
            }
        ))

        descriptors.append(_create_descriptor(
            template, {
                'name': 'stats_avg_query_%s' % db,
                'units': 'milliseconds',
                'description': 'average query duration in milliseconds'
            }
        ))

def _init_pool_descriptors(descriptors, template):

    template = copy.deepcopy(template)
    template['groups'] = 'pgBouncerPools'

    for pool in _POOLS.keys():

        descriptors.append(_create_descriptor(
            template, {
                'name': 'pool_cl_active_%s' % pool,
                'units': '# of connections',
                'description': 'count of currently active client connections'
            }
        ))

        descriptors.append(_create_descriptor(
            template, {
                'name': 'pool_cl_waiting_%s' % pool,
                'units': '# of connections',
                'description': 'count of currently waiting client connections'
            }
        ))

        descriptors.append(_create_descriptor(
            template, {
                'name': 'pool_sv_active_%s' % pool,
                'units': '# of connections',
                'description': 'count of currently active server connections'
            }
        ))

        descriptors.append(_create_descriptor(
            template, {
                'name': 'pool_sv_idle_%s' % pool,
                'units': '# of connections',
                'description': 'count of currently idle server connections'
            }
        ))

        descriptors.append(_create_descriptor(
            template, {
                'name': 'pool_sv_used_%s' % pool,
                'units': '# of connections',
                'description': 'count of currently used server connections'
            }
        ))

        descriptors.append(_create_descriptor(
            template, {
                'name': 'pool_sv_tested_%s' % pool,
                'units': '# of connections',
                'description': 'count of currently tested server connections'
            }
        ))

        descriptors.append(_create_descriptor(
            template, {
                'name': 'pool_sv_login_%s' % pool,
                'units': '# of connections',
                'description': 'count of server connections currently logged into PostgreSQL'
            }
        ))

        descriptors.append(_create_descriptor(
            template, {
                'name': 'pool_maxwait_%s' % pool,
                'units': 'seconds',
                'description': 'how long the first (oldest) client in queue has waited, in seconds'
            }
        ))

def _init_descriptors():

    global _DESCRIPTORS

    _template = dict(
        name='XYZ',
        call_back=metric_handler,
        time_max=30,
        value_type='uint',
        format='%d',
        groups='pgBouncer'
    )

    _DESCRIPTORS = []

    _init_dbstats_descriptors(_DESCRIPTORS, _template)
    _init_pool_descriptors(_DESCRIPTORS, _template)

    return _DESCRIPTORS

# Metric descriptors are initialized here 
def metric_init(params):

    # init dsn
    _init_dsn(params)

    # init databases
    _init_databases(params)

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
        "host":"host_here",
        "port":"port_here",
        "user":"user_here",
        "password":"password_here",
        "sslmode": "disable",
        "databases": ""
    })

    while True:
        for d in descriptors:
            v = d['call_back'](d['name'])
            print 'value for %s is %u' % (d['name'],  v)
        time.sleep(5)
