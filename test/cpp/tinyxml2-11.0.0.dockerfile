FROM applerodite/repohcl-base

WORKDIR /root/resource

ENV ROOT=tinyxml2-11.0.0

RUN wget https://github.com/leethomason/tinyxml2/archive/refs/tags/11.0.0.zip && \
    unzip 11.0.0.zip && \
    rm 11.0.0.zip

WORKDIR /root/

ADD metrics/parse.sc /root/metrics/parse.sc

RUN mkdir -p /root/resource/${ROOT} && \
    mkdir -p /root/output/${ROOT} && \
    joern --script metrics/parse.sc --param path=/root/resource/${ROOT} --param output=/root/output/${ROOT} && \
    ctags -R --languages=C,C++ --c-kinds=p -f /root/output/${ROOT}/tags /root/resource/${ROOT}

WORKDIR /root
CMD ["python3", "main.py", "resource/${ROOT}", "--lang", "cpp"]