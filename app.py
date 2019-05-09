import multiprocessing
import os
from random import randint
from time import sleep

import click
import psycopg2
import sys
from tqdm import tqdm
from influxdb import InfluxDBClient
from faker import Faker


fake = Faker()
client = InfluxDBClient(os.getenv('INFLUX_HOST'),
                        8086,
                        os.getenv('INFLUX_USER'),
                        os.getenv('INFLUX_PASSWORD'),
                        os.getenv('INFLUX_DATABASE'))

client.create_database('ask')


@click.group()
def cli():
    pass


def connect():
    try:
        connection = psycopg2.connect(host=os.getenv('DATABASE_HOST'),
                                      dbname=os.getenv('DATABASE_NAME'),
                                      user=os.getenv('DATABASE_USER'),
                                      password=os.getenv('DATABASE_PASSWORD'))

        return connection
    except psycopg2.DatabaseError as e:
        print(f'Error {e}')
        sys.exit(1)


@cli.command()
@click.option('--rows', default=10000, help='Number of records to be generated')
def seed_database(rows):
    connection = connect()
    connection.autocommit = True
    cursor = connection.cursor()

    print('Creating table example...')
    cursor.execute('DROP TABLE IF EXISTS invoices')
    cursor.execute("""
    CREATE TABLE invoices (
      id serial PRIMARY KEY,
      invoice_number integer NOT NULL,
      invoice_date date NOT NULL,
      invoice_due_date date NOT NULL,
      customer_name varchar(255) NOT NULL,
      customer_address varchar(255) DEFAULT NULL,
      customer_zip_code varchar(10) DEFAULT NULL,
      customer_city varchar(255) NOT NULL,
      customer_country varchar(3) NOT NULL,
      company_name varchar(255) NOT NULL,
      company_address varchar(255) DEFAULT NULL,
      company_zip_code varchar(10) DEFAULT NULL,
      company_city varchar(255) NOT NULL,
      company_country varchar(3) NOT NULL,
      amount decimal(10,5) NOT NULL,
      vat decimal(10,5) NOT NULL,
      total_amount decimal(10,5) NOT NULL
    )""")

    _insert_rows(cursor, rows)


def _insert_rows(cursor, rows):
    batch_size = 25000
    values = []

    for index in tqdm(range(1, rows)):
        values.append((
            index,
            "2019-05-01",
            "2019-05-01",
            fake.name(),
            fake.address(),
            "00-000",
            "Krakow",
            "PL",
            fake.name(),
            fake.address(),
            "00-000",
            "Krakow",
            "PL",
            100,
            23,
            123
        ))

        if len(values) >= batch_size:
            cursor.executemany("""
            INSERT INTO invoices 
            (
                invoice_number,
                invoice_date,
                invoice_due_date,
                customer_name,
                customer_address,
                customer_zip_code,
                customer_city,
                customer_country,
                company_name,
                company_address,
                company_zip_code,
                company_city,
                company_country,
                amount,
                vat,
                total_amount
            )
            VALUES %s;
            """, [[v] for v in values])
            values = []


def stress_test_worker(_):
    connection = connect()
    connection.autocommit = True

    cursor = connection.cursor()
    index = 1
    while True:
        print(f'Query {index}')

        for i in range(randint(0, 100)):
            cursor.execute('SELECT * FROM invoices WHERE id={}'.format(i))

        _insert_rows(cursor, randint(0, 10000))

        cursor.execute(f'BEGIN TRANSACTION;'
                       f'SELECT i2.customer_name, COUNT(*), AVG(i2.total_amount), \'{index}\' '
                       f'FROM invoices i '
                       f'LEFT JOIN invoices i2 ON i2.company_name = i.company_name '
                       f'GROUP BY i2.customer_address, i2.customer_name '
                       f'ORDER BY i2.customer_name; '
                       f'COMMIT;')
        index += 1


@cli.command()
def stress_test():
    pool = multiprocessing.Pool(processes=2)

    try:
        pool.map(stress_test_worker, list(range(8)))
        pool.close()
    except KeyboardInterrupt:
        pool.terminate()
    except Exception as e:
        print(e)
        pool.terminate()
    finally:
        pool.join()


@cli.command()
def collect_metrics():
    connection = connect()
    cursor = connection.cursor()

    transactions_sum = 0

    while True:
        print('Collecting metrics...')
        _log_table_sizes(cursor)
        _log_long_running_queries(cursor)
        _log_indexes(cursor)
        transactions_sum = _log_transactions(cursor, transactions_sum)
        sleep(0.5)


def _log_transactions(cursor, last_transactions_sum):
    cursor.execute('BEGIN TRANSACTION;')
    cursor.execute('SELECT sum(xact_commit+xact_rollback) FROM pg_stat_database;')

    current_transactions_sum = cursor.fetchone()[0]
    print('Current transactions', current_transactions_sum)

    cursor.execute('COMMIT;')

    if last_transactions_sum != 0 and (current_transactions_sum - last_transactions_sum > 0):
        client.write_points([{
            'measurement': 'transactions',
            'tags': {
                'type': 'number_of_transactions'
            },
            'fields': {
                'count': int(current_transactions_sum - last_transactions_sum)
            }
        }])

    return current_transactions_sum


def _log_long_running_queries(cursor):
    cursor.execute("""
    SELECT
      pid,
      now() - pg_stat_activity.query_start AS duration,
      query,
      state
    FROM pg_stat_activity
    WHERE (now() - pg_stat_activity.query_start) > interval '1 seconds';
    """)

    points = []

    for pid, duration, query, state in cursor.fetchall():
        points.append({
            'measurement': 'long_running_queries',
            'tags': {
                'type': 'query'
            },
            'fields': {
                'pid': pid,
                'duration_ms': (duration.seconds * 1000) + duration.microseconds / 1000,
                'query': '"{}"'.format(query.replace(',', '\,').replace('"', '')),
                'state': state
            }
        })

    client.write_points(points)


def _log_indexes(cursor):
    cursor.execute('SELECT schemaname, relname, indexrelname, idx_scan, idx_tup_read, idx_tup_fetch FROM pg_stat_user_indexes')

    points = []

    for schemaname, relname, indexrelname, idx_scan, idx_tup_read, idx_tup_fetch in cursor.fetchall():
        points.append({
            'measurement': 'indexes',
            'tags': {
                'schema': schemaname,
                'relation_name': relname,
                'index_rel_name': indexrelname
            },
            'fields': {
                'idx_scan': idx_scan,
                'idx_tup_read': idx_tup_read,
                'idx_tup_fetch': idx_tup_fetch
            }
        })

    client.write_points(points)


def _log_table_sizes(cursor):
    cursor.execute("""
    SELECT
        schema_name,
        relname,
        table_size
    FROM (
       SELECT
         pg_catalog.pg_namespace.nspname AS schema_name,
         relname,
         pg_relation_size(pg_catalog.pg_class.oid) AS table_size

       FROM pg_catalog.pg_class
         JOIN pg_catalog.pg_namespace ON relnamespace = pg_catalog.pg_namespace.oid
    ) t
    WHERE schema_name NOT LIKE 'pg_%'
    GROUP BY schema_name, relname, table_size
    ORDER BY table_size DESC
    """)

    points = []
    for schema, table, size_in_bytes in cursor.fetchall():
        points.append({
            'measurement': 'table_size',
            'tags': {
                'schema': schema,
                'table': table,
            },
            'fields': {
                'size_in_bytes': size_in_bytes
            }
        })

    client.write_points(points)


if __name__ == '__main__':
    cli()
