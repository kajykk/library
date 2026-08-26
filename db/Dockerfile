FROM postgres:16

# 编译安装 scws + zhparser（中文分词），首次构建后镜像可复用。
# 版本固定：scws 1.2.3（官方源，https 优先）；zhparser v2.3（GitHub release tag，
# 此前拉 master 动态 tarball，不可复现且有供应链风险）。
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential postgresql-server-dev-16 bzip2 wget ca-certificates \
    && cd /tmp \
    && (wget -q https://www.xunsearch.com/scws/down/scws-1.2.3.tar.bz2 \
        || wget -q http://www.xunsearch.com/scws/down/scws-1.2.3.tar.bz2) \
    && tar xjf scws-1.2.3.tar.bz2 \
    && cd scws-1.2.3 \
    && ./configure \
    && make -j"$(nproc)" \
    && make install \
    && ldconfig \
    && cd /tmp \
    && wget -q https://github.com/amutu/zhparser/archive/refs/tags/v2.3.tar.gz -O zhparser.tar.gz \
    && tar zxf zhparser.tar.gz \
    && cd zhparser-2.3 \
    && make -j"$(nproc)" SCWS_HOME=/usr/local \
    && make install SCWS_HOME=/usr/local \
    && ldconfig \
    && apt-get purge -y build-essential postgresql-server-dev-16 bzip2 wget ca-certificates \
    && apt-get autoremove -y \
    && rm -rf /tmp/* /var/lib/apt/lists/*

COPY 00-zhparser.sql /docker-entrypoint-initdb.d/
