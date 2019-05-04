FROM python:3.7-alpine

RUN apk add linux-headers musl-dev postgresql-dev python3-dev gcc

WORKDIR /monitoring

COPY . .

RUN pip install -r requirements.txt
