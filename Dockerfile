FROM ubuntu:22.04

USER root

RUN sed -i s@/archive.ubuntu.com/@/mirrors.aliyun.com/@g /etc/apt/sources.list && \
    sed -i s@/security.ubuntu.com/@/mirrors.aliyun.com/@g /etc/apt/sources.list && \
    apt-get clean && \
    apt-get update --fix-missing && \
    apt-get install python3 python3-pip -y && \
    apt-get clean -y && \
      rm -rf \
      /var/cache/debconf/* \
      /var/lib/apt/lists/* \
      /var/log/* \
      /var/tmp/* && \
      rm -rf /tmp/*

RUN mkdir -p /app

COPY ./requirements.txt /tmp/requirements.txt

#RUN pip3 install -r /app/requirements.txt -i https://mirrors.aliyun.com/pypi/simple --trusted-host mirrors.aliyun.com
RUN pip install --no-cache-dir -r /tmp/requirements.txt -i http://mirrors.cloud.tencent.com/pypi/simple --trusted-host mirrors.cloud.tencent.com

WORKDIR /app
COPY . /app

CMD uvicorn main:app --host 0.0.0.0 --port 8080
