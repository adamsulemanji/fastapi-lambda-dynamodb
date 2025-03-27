## Building and Running the Docker Container

This docker image is replicated for both ```dev``` and ```prod``` environments using cloud development. The image is built using the `Dockerfile` in the root directory of the project.  image as the base image. The image is built using the following steps:

### Build the Docker Image
```sh
docker build -t fastapi-local -f Dockerfile.dev .
```

### Run the Docker Container
Pass the development table name as an environment variable at runtime:
```sh
docker run \
  -p 8000:8000 \
  -e TABLE_NAME="MyTable-dev" \
  -v ~/.aws:/root/.aws:ro \
  -e AWS_PROFILE=default \
  -e AWS_REGION_NAME=us-east-1 \
  -e AWS_COGNITO_APP_CLIENT_ID=6ib0lefeskn33ugk4nc08uqtt7 \
  -e AWS_COGNITO_USER_POOL_ID=us-east-1_ETXEnpSV3 \
  fastapi-local
```

To stop the docker image:
```sh
docker stop $(docker ps -q --filter ancestor=fastapi-lambda)
```

or Ctrl+C in the terminal.

### Access the API specifications

Visit http://localhost:8000/docs or http://localhost:8000/redoc to view the API documentation.

