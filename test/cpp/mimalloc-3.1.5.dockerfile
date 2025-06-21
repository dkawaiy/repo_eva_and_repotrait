FROM applerodite/repohcl-base

WORKDIR /root/resource

ENV ROOT=mimalloc-3.1.5

RUN wget https://github.com/microsoft/mimalloc/archive/refs/tags/v3.1.5.zip && \
    unzip v3.1.5.zip && \
    rm v3.1.5.zip

WORKDIR /root/

ADD metrics/parse.sc /root/metrics/parse.sc

RUN mkdir -p /root/resource/${ROOT} && \
    mkdir -p /root/output/${ROOT} && \
    joern --script metrics/parse.sc --param path=/root/resource/${ROOT} --param output=/root/output/${ROOT} && \
    ctags -R --languages=C,C++ --c-kinds=p -f /root/output/${ROOT}/tags /root/resource/${ROOT}

WORKDIR /root
CMD ["python3", "main.py", "resource/${ROOT}", "--lang", "cpp"]