FROM postgres:16

# 编译安装 scws + zhparser（中文分词），首次构建后镜像可复用
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential postgresql-server-dev-16 bzip2 wget ca-certificates \
    && cd /tmp \
    && wget -q http://www.xunsearch.com/scws/down/scws-1.2.3.tar.bz2 \
    && tar xjf scws-1.2.3.tar.bz2 \
    && cd scws-1.2.3 \
    && ./configure \
    && make -j"$(nproc)" \
    && make install \
    && ldconfig \
    && cd /tmp \
    && wget -q https://github.com/amutu/zhparser/archive/refs/heads/master.tar.gz -O zhparser.tar.gz \
    && tar zxf zhparser.tar.gz \
    && cd zhparser-master \
    && make -j"$(nproc)" SCWS_HOME=/usr/local \
    && make install SCWS_HOME=/usr/local \
    && ldconfig \
    && apt-get purge -y build-essential postgresql-server-dev-16 bzip2 wget \
    && apt-get autoremove -y \
    && rm -rf /tmp/* /var/lib/apt/lists/*

COPY 00-zhparser.sql /docker-entrypoint-initdb.d/
