IMAGE := fastqc:0.12.1
SELF_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
ROOT := $(abspath $(SELF_DIR)/../..)
IMG_DIR := $(abspath $(SELF_DIR))
SAMPLES := $(ROOT)/samples

.PHONY: build rebuild size run clean

build:
	@echo "Building $(IMAGE) with cache from $(IMG_DIR)..."
	time docker build -t $(IMAGE) -f $(IMG_DIR)/Dockerfile $(IMG_DIR)

rebuild:
	@echo "Building $(IMAGE) without cache from $(IMG_DIR)..."
	time docker build --no-cache -t $(IMAGE) -f $(IMG_DIR)/Dockerfile $(IMG_DIR)

size:
	docker images $(IMAGE)

run:
	mkdir -p $(SAMPLES)/out
	docker run --rm -v $(SAMPLES):/data $(IMAGE) /data/test.fastq --outdir /data/out --quiet
	@ls -lh $(SAMPLES)/out

clean:
	rm -rf $(SAMPLES)/out
