FROM applerodite/repohcl-base

WORKDIR /root/resource

ENV ROOT=rapidxml-1.13

RUN wget https://github.com/CodeFinder2/rapidxml/archive/refs/heads/main.zip && \
    unzip main.zip && \
    rm main.zip && \
    mv rapidxml-main ${ROOT}

WORKDIR /root/

ADD metrics/parse.sc /root/metrics/parse.sc

RUN mkdir -p /root/resource/${ROOT} && \
    mkdir -p /root/output/${ROOT} && \
    joern --script metrics/parse.sc --param path=/root/resource/${ROOT} --param output=/root/output/${ROOT} && \
    ctags -R --languages=C,C++ --c-kinds=p -f /root/output/${ROOT}/tags /root/resource/${ROOT}

WORKDIR /root
CMD ["python3", "main.py", "resource/${ROOT}", "--lang", "cpp"]