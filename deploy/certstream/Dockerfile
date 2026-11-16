FROM golang:1.27-alpine@sha256:fa3d596898d9f126af7c01d14ee7597bab3f39cccf392ef36b69fe77cd681f18 AS build
RUN apk add --no-cache ca-certificates git patch
WORKDIR /src
ADD --checksum=sha256:be62429873d0b376fb0f522a46e2bbcf15a2d013c87c10d13878ed29b4a7fcd4 https://codeload.github.com/d-Rickyy-b/certstream-server-go/tar.gz/refs/tags/v1.10.1 /tmp/source.tar.gz
RUN tar -xzf /tmp/source.tar.gz --strip-components=1 -C /src
COPY recovery.patch /tmp/recovery.patch
RUN patch -p1 < /tmp/recovery.patch \
    && go test ./internal/certificatetransparency -run TestLeakScopeRecoveryStart -count=1 \
    && CGO_ENABLED=0 go build -trimpath -ldflags='-s -w -X github.com/d-Rickyy-b/certstream-server-go/internal/config.Version=1.10.1-leakscope.1' -o /certstream-server ./cmd/certstream-server-go

FROM alpine:3.24@sha256:3f4d6b32e26703c6082839234f43769d5046b3ac7c910033911d1e71d8cd630a
RUN apk add --no-cache ca-certificates \
    && adduser -D -H -u 501 certstream
COPY --from=build /certstream-server /app/certstream-server
COPY --from=build /src/LICENSE /licenses/certstream-server-go-LICENSE
LABEL org.opencontainers.image.version="1.10.1-leakscope.1" \
      org.opencontainers.image.source="https://github.com/d-Rickyy-b/certstream-server-go/tree/v1.10.1"
WORKDIR /app
USER 501:20
EXPOSE 8080
ENTRYPOINT ["/app/certstream-server", "--config", "/app/config/config.yaml"]
