FROM golang:1.18-alpine AS builder

WORKDIR /app
COPY . .

RUN go mod tidy
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o /app/main .

FROM alpine:latest
RUN apk --no-cache add ca-certificates

WORKDIR /app
COPY --from=builder /app/main .

ENV TABLE_NAME=MyTable-prod
ENV STAGE=prod

ENTRYPOINT ["/app/main"] 