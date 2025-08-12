# ---- builder: download FastQC ----
FROM debian:bookworm-slim AS builder
ARG FASTQC_VERSION=0.12.1
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl unzip \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /opt
RUN curl -fsSL -o fastqc.zip \
  https://www.bioinformatics.babraham.ac.uk/projects/fastqc/fastqc_v${FASTQC_VERSION}.zip \
 && unzip fastqc.zip \
 && rm fastqc.zip \
 && chmod +x FastQC/fastqc

# ---- runtime: JRE + perl only ----
FROM debian:bookworm-slim
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends openjdk-17-jre-headless perl \
 && rm -rf /var/lib/apt/lists/*
ENV PATH=/opt/FastQC:$PATH
COPY --from=builder /opt/FastQC /opt/FastQC
ENTRYPOINT ["fastqc"]
