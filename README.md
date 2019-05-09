# Metryki z bazy danych Postgresql
### Założenia

- Konfiguracja za pomocą Dockera i Docker Compose
- Przykładowa baza danych z dużą ilością danych (w tym przypadku tabela z fakturami `invoices`)
- Zbieranie metryk z bazy danych
  - Długo wykonujące się zapytania
  - Liczba transakcji
  - Liczba indeksów
  - Load
  - Obciążenie dysku (IO Top)
  - Objętość baz danych/tabel
- Jako test będziemy wykonywać w pętli dużą liczbę wolnych zapytań
- Na podstawie artykułu: https://www.datadoghq.com/blog/postgresql-monitoring-tools/



### Architektura

Kontener z aplikacją zbiera metryki z tabel w bazie Postgresql, przetwarza te wpisy i wysyła do bazy InfluxDB, skąd aplikacja do wizualicji - Grafana może je wyciągnąć i wyświetlić w webowym interfejsie użytkownika.



### Źródła metryk

- Długo wykonujące się zapytania - `pg_stat_activity`

  ```
  SELECT
    pid,
    now() - pg_stat_activity.query_start AS duration,
    query,
    state
  FROM pg_stat_activity
  WHERE (now() - pg_stat_activity.query_start) > interval '1 seconds';
  ```

  ```
  > select * from long_running_queries limit 1;
  name: long_running_queries
  time                duration_ms pid query                                                                                                                                                                                                                  state  type
  ----                ----------- --- -----                                                                                                                                                                                                                  -----  ----
  1557424247545965805 1211.335    102 "SELECT i2.customer_name\, COUNT(*)\, AVG(i2.total_amount)\, '29' FROM invoices i LEFT JOIN invoices i2 ON i2.company_name = i.company_name GROUP BY i2.customer_address\, i2.customer_name ORDER BY i2.customer_name" active query
  ```

  

- Rozmiar tabel

  ```
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
  ```

  ```
  > select * from table_size where schema='public' limit 5;
  name: table_size
  time                schema size_in_bytes table
  ----                ------ ------------- -----
  1556970504345458691 public 1359872       invoices
  ```

- Liczba transakcji

  ```
  SELECT sum(xact_commit+xact_rollback) FROM pg_stat_database;
  ```

  Liczymy różnicę między sumami jako liczbę transakcji

### Test wydajnościowy

```
SELECT i2.customer_name, COUNT(*), AVG(i2.total_amount), \'{index}\' '
f'FROM invoices i '
f'LEFT JOIN invoices i2 ON i2.company_name = i.company_name '
f'GROUP BY i2.customer_address, i2.customer_name '
f'ORDER BY i2.customer_name
```

Mamy nieefektywnego joina, grupowanie, sortowania, potencjalnie wolne zapytanie



### Konfiguracja środowiska

```
docker-compose up
docker-compose run monitoring python app.py seed-database --rows 100000

# In one terminal
docker-compose run monitoring python app.py collect-metrics

# In another launch stress tests
docker-compose run monitoring python app.py stress-tests
```

