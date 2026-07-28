# Commands
## certs
cd nginx/ && mkdir -p certs && openssl req -x509 -nodes -days 365 -newkey rsa:2048 -keyout certs/localhost.key -out certs/localhost.crt -subj "/CN=localhost"

## env var's
cp .env.example .env


