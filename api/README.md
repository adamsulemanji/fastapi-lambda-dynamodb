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
  -e MOVIES_TABLE_NAME="movies" \
  -e MOVIES_JOBS_TABLE_NAME="movies-jobs" \
  -e MOVIES_JOBS_USERNAME_INDEX="username-created_at-index" \
  -e MOVIES_SCRAPE_QUEUE_URL="https://sqs.us-east-1.amazonaws.com/<account-id>/<queue-name>" \
  -e MOVIES_POSTER_JOBS_TABLE_NAME="movies-poster-jobs" \
  -e MOVIES_POSTER_JOBS_USERNAME_INDEX="username-created_at-index" \
  -e MOVIES_POSTER_STATE_MACHINE_ARN="arn:aws:states:us-east-1:<account-id>:stateMachine:<name>" \
  -e MOVIES_POSTER_OUTPUT_BUCKET_NAME="<poster-output-bucket>" \
  -e MOVIES_POSTER_URL_EXPIRATION_SECONDS="604800" \
  fastapi-local
```

To stop the docker image:
```sh
docker stop $(docker ps -q --filter ancestor=fastapi-lambda)
```

or Ctrl+C in the terminal.

### Access the API specifications

Visit http://localhost:8000/docs or http://localhost:8000/redoc to view the API documentation.

## Async Movies Scraping

Movies scraping now runs asynchronously through SQS + worker Lambda.

### Create a scrape job
```sh
curl -X POST http://localhost:8000/movies/jobs \
  -H "Content-Type: application/json" \
  -d '{"username":"some_user","force":false}'
```

### Check job status
```sh
curl http://localhost:8000/movies/jobs/<job_id>
```

### Backfill alias (also async)
```sh
curl -X POST "http://localhost:8000/movies/backfill?username=some_user&force=true"
```

### Cached search with async refresh trigger
```sh
curl "http://localhost:8000/movies/search?username=some_user&limit=10&fast=false&async_refresh=true"
```
If cache is stale/missing, the response returns immediately and includes `X-Movies-Job-Id`.

## Async Movie Poster Jobs

Create a color-sorted diary poster job:
```sh
curl -X POST http://localhost:8000/movies/poster-jobs \
  -H "Content-Type: application/json" \
  -d '{"username":"some_user","movie_count":0}'
```

Check poster job status:
```sh
curl http://localhost:8000/movies/poster-jobs/<job_id>
```

`movie_count` behavior:
- `0`: use all logged movies
- `1+`: use that many movies (or all available if fewer exist)

Layout behavior:
- auto-selects a perfect rectangle with no trailing gaps
- if the selected movie count is prime, adds one blank tile

When the job reaches `SUCCEEDED`, `output_url` is returned as a presigned S3 URL for the generated PNG.
