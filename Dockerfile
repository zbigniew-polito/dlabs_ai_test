FROM tiangolo/uvicorn-gunicorn-fastapi:python3.7

WORKDIR /app
RUN apt update && apt-get -y install nginx nginx-full certbot

COPY gunicorn_conf.py /
COPY start.sh /
COPY start-reload.sh /

RUN chmod +x /start-reload.sh
RUN chmod +x /start.sh
RUN chmod +x /gunicorn_conf.py

COPY nginx.conf /etc/nginx

RUN mkdir /var/run/gunicorn
RUN mkdir /var/log/gunicorn
RUN mkdir /etc/nginx/keys

COPY key.key /etc/nginx/keys
COPY crt.crt /etc/nginx/keys


ENV BIND=unix:/var/run/gunicorn/gunicorn.sock \
    ACCESS_LOG=/var/log/gunicorn/access.log \
    ERROR_LOG=/var/log/gunicorn/error.log \
    MODULE_NAME=server.server

RUN pip3 install "poetry"

COPY poetry.lock pyproject.toml /app/

RUN poetry config virtualenvs.create false && poetry install --no-interaction --no-ansi


COPY . /app