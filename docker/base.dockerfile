FROM ubuntu:20.04

RUN sed -i 's@archive.ubuntu.com@mirrors.aliyun.com@g' /etc/apt/sources.list && \
    sed -i 's@security.ubuntu.com@mirrors.aliyun.com@g' /etc/apt/sources.list

# 安装依赖
RUN apt update && \
    apt install -y wget unzip universal-ctags openjdk-21-jdk bzip2 xz-utils gzip software-properties-common && \
    add-apt-repository -y ppa:deadsnakes/ppa && \
    apt install -y python3.12 python3.12-venv && \
    apt clean && \
    rm -rf /var/lib/apt/lists/*

ENV JOERN_HOME=/joern
ENV PATH="$JOERN_HOME:$PATH"

# 安装joern，删除不需要的前端
RUN wget https://github.com/joernio/joern/releases/download/v4.0.353/joern-cli.zip && \
    unzip joern-cli.zip && \
    mv joern-cli ${JOERN_HOME} && \
    rm -rf joern-cli.zip && \
    rm -rf ${JOERN_HOME}/frontends/csharpsrc2cpg && \
    rm -rf ${JOERN_HOME}/frontends/ghidra2cpg && \
    rm -rf ${JOERN_HOME}/frontends/gosrc2cpg && \
    rm -rf ${JOERN_HOME}/frontends/swiftsrc2cpg && \
    rm -rf ${JOERN_HOME}/frontends/pysrc2cpg && \
    rm -rf ${JOERN_HOME}/frontends/rubysrc2cpg && \
    rm -rf ${JOERN_HOME}/frontends/php2cpg && \
    rm -rf ${JOERN_HOME}/frontends/jimple2cpg && \
    chmod +x ${JOERN_HOME}/joern

ENV ARKANALYZER_HOME=/arkanalyzer

# 克隆 arkanalyzer 仓库到 /arkanalyzer，并安装 Node.js、npm、typescript，npm install 仓库依赖
RUN apt update && apt install -y git curl ca-certificates && \
    # 安装 Node.js 18 (Nodesource)
    curl -fsSL https://deb.nodesource.com/setup_18.x | bash - && \
    apt install -y nodejs && \
    # 全局安装 typescript
    npm install -g typescript && \
    # 克隆仓库并安装 npm 依赖（如果有 package.json）
    git clone https://gitcode.com/openharmony-sig/arkanalyzer.git $ARKANALYZER_HOME && \
    if [ -f "$ARKANALYZER_HOME/package.json" ]; then \
      cd $ARKANALYZER_HOME && npm install --unsafe-perm; \
    fi && \
    apt-get purge -y git && \
    apt-get autoremove -y && \
    apt clean && rm -rf /var/lib/apt/lists/*