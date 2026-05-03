IMAGE_NAME=autumnix/td-hr-pruner
LOCAL_IMAGE=td-hr-pruner:test
VERSION?=v0.1.2
PLATFORMS=linux/amd64,linux/arm64
BUILDER?=multiarch-builder

docker-build:
	docker build -t $(LOCAL_IMAGE) .

docker-run:
	docker run --rm $(LOCAL_IMAGE) --once --dry-run --verbose

buildx-create:
	@if ! docker buildx inspect $(BUILDER) >/dev/null 2>&1; then \
		docker buildx create --use --name $(BUILDER); \
	else \
		docker buildx use $(BUILDER); \
	fi
	docker buildx inspect --bootstrap

docker-release: buildx-create
	docker buildx build \
		--platform $(PLATFORMS) \
		-t $(IMAGE_NAME):latest \
		-t $(IMAGE_NAME):$(VERSION) \
		--push .

release: docker-release
