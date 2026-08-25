#!/bin/bash
# This script is used to set up the environment for the project.

echo "Creating the Certificates..." 
cd nginx/ 
mkdir -p certs
sudo openssl req -x509 -nodes -days 365 -newkey rsa:2048 -keyout certs/localhost.key -out certs/localhost.crt -subj "/CN=localhost"
echo "Certificates created successfully."

cd ..

echo "Setting up the environment variables..."
cp .env.example .env
echo "Environment variables set up successfully."

echo "Installing dependencies..."
sudo apt update
sudo apt upgrade -y
sudo apt install -y docker.io docker-compose
echo "Dependencies installed successfully."

echo "Building and starting the Docker containers..."
sudo docker-compose up -d --build
echo "Docker containers built and started successfully."
