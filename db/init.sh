#!/bin/bash
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE USER ask;
    CREATE DATABASE ask;
    GRANT ALL PRIVILEGES ON DATABASE ask TO ask;
EOSQL