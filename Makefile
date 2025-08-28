IMAGES_DIR ?= images

REGION ?= northamerica-northeast1
PROJECT_ID ?= gtex-pipeline
REPO ?= rnaseq
PLATFORM ?= linux/amd64

FASTQC_TAG ?= 0.12.1
MULTIQC_TAG ?= 1.29.0-kaleido

AR := $(REGION)-docker.pkg.dev/$(PROJECT_ID)/$(REPO)

.PHONY: login show build-fastqc build-multiqc

login:
	gcloud auth configure-docker $(REGION)-docker.pkg.dev -q

show:
	@echo "FASTQC => $(AR)/fastqc:$(FASTQC_TAG)"
	@echo "MULTIQC => $(AR)/multiqc:$(MULTIQC_TAG)"

build-fastqc:
	docker buildx build --platform $(PLATFORM) -f Dockerfile.fastqc -t $(AR)/fastqc:$(FASTQC_TAG) --push $(IMAGES_DIR)

build-multiqc:
	docker buildx build --platform $(PLATFORM) -f Dockerfile.multiqc -t $(AR)/multiqc:$(MULTIQC_TAG) --push $(IMAGES_DIR)
