FROM python:3.10-slim-bullseye

LABEL maintainer="kuangdd@qq.com"
ARG TZ='Asia/Shanghai'

ARG CHATGPT_ON_WECHAT_VER

RUN echo /etc/apt/sources.list
# RUN sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list
ENV BUILD_PREFIX=/app

RUN mkdir -p ${BUILD_PREFIX}

RUN apt-get update \
    &&apt-get install -y --no-install-recommends bash vim

#RUN mkdir -p /home/noroot \
#    && groupadd -r noroot \
#    && useradd -r -g noroot -s /bin/bash -d /home/noroot noroot \
#    && chown -R noroot:noroot /home/noroot ${BUILD_PREFIX} /usr/local/lib

#USER noroot

COPY ./requirements.txt /tmp/requirements.txt

RUN pip install -r /tmp/requirements.txt -i https://mirrors.aliyun.com/pypi/simple --trusted-host mirrors.aliyun.com
#RUN pip install --no-cache-dir -r /tmp/requirements.txt -i http://mirrors.cloud.tencent.com/pypi/simple --trusted-host mirrors.cloud.tencent.com

WORKDIR ${BUILD_PREFIX}

COPY . ${BUILD_PREFIX}

CMD uvicorn main:app --host 0.0.0.0 --port 8080
