### Migration Plan: AWS to Dell PowerEdge R430

This guide outlines the steps to migrate your FastAPI application and PostgreSQL database from your current AWS environment to a dedicated Dell PowerEdge R430 server.

The most robust and recommended method for this migration is to use **Docker**, as your project already contains a `Dockerfile` and `docker-compose.yml`. This approach encapsulates your application and its dependencies, making the migration smoother and future deployments more consistent.

---

### Part 1: Server Preparation

1.  **Install a Linux Operating System**:
    *   It is recommended to install a stable, server-focused Linux distribution. **Ubuntu Server 22.04 LTS** is an excellent choice.
    *   During installation, ensure you configure networking correctly so the server is accessible on your network.
    *   Create a non-root user account with `sudo` privileges for managing the server.

2.  **Install Docker and Docker Compose**:
    *   Once the OS is running, you will need to install Docker Engine and Docker Compose.
    *   Log in to your server and run the following commands:

    ```bash
    # Update package lists
    sudo apt-get update

    # Install prerequisites
    sudo apt-get install -y ca-certificates curl gnupg

    # Add Docker's official GPG key
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg

    # Set up the Docker repository
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
      $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
      sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

    # Install Docker Engine and Docker Compose
    sudo apt-get update
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    ```

3.  **Configure Firewall**:
    *   Enable the firewall to secure your server. You need to allow SSH, HTTP, and HTTPS traffic.

    ```bash
    sudo ufw allow ssh
    sudo ufw allow 80/tcp   # For HTTP
    sudo ufw allow 443/tcp  # For HTTPS
    sudo ufw enable
    ```

---

### Part 2: Database Migration

1.  **Back Up the AWS RDS PostgreSQL Database**:
    *   From your local machine or a machine that has access to your AWS RDS instance, use the `pg_dump` utility to create a backup of your production database.

    ```bash
    pg_dump "postgresql://<user>:<password>@<aws_rds_endpoint>:<port>/<database_name>" -F c -b -v -f production_backup.dump
    ```
    *   This command creates a compressed backup file named `production_backup.dump`.

2.  **Transfer Backup to the New Server**:
    *   Use `scp` to securely copy the backup file to your new Dell server.

    ```bash
    scp production_backup.dump <your_user>@<dell_server_ip>:~/
    ```

3.  **Restore the Database on the New Server**:
    *   The `docker-compose.yml` in your project is already configured to run a PostgreSQL container. When you start the application with `docker-compose`, it will automatically create a new, empty PostgreSQL database.
    *   Once the container is running, copy the backup file into the PostgreSQL container and restore it.

    ```bash
    # Find the name of your postgres container
    docker ps

    # Copy the dump file into the container (replace <container_name>)
    docker cp ~/production_backup.dump <container_name>:/tmp/production_backup.dump

    # Restore the database (replace <container_name> and <database_name>)
    docker exec -it <container_name> pg_restore --verbose --clean --no-acl --no-owner -d <database_name> /tmp/production_backup.dump
    ```

---

### Part 3: Application Deployment

1.  **Clone Your Application Code**:
    *   On the new Dell server, install `git` and clone your project repository.

    ```bash
    sudo apt-get install -y git
    git clone <your_repository_url>
    cd <your_project_directory>
    ```

2.  **Review Configuration**:
    *   Your `docker-compose.yml` file defines the services, networks, and volumes. The database connection from your FastAPI app to the PostgreSQL container is handled internally by Docker networking (e.g., using the service name `db`).
    *   Ensure that any environment variables required for the application (e.g., `DATABASE_URL`, `SECRET_KEY`) are correctly defined in your `docker-compose.yml` or a `.env` file that it uses.

3.  **Build and Run the Application**:
    *   From your project directory on the Dell server, use Docker Compose to build and run your entire application stack.

    ```bash
    docker-compose up --build -d
    ```
    *   `--build`: Forces Docker to rebuild the images, which is good for the first run.
    *   `-d`: Runs the containers in detached mode (in the background).

4.  **Verify the Application**:
    *   Check the logs to ensure all services started correctly.

    ```bash
    docker-compose logs -f
    ```
    *   Open a web browser and navigate to your server's IP address (`http://<dell_server_ip>`). You should see your application's login page or main interface.

---

### Part 4: Final Steps

1.  **Set Up NGINX as a Reverse Proxy**:
    *   For a production environment, you should not expose the FastAPI development server directly. An NGINX reverse proxy will handle incoming HTTP/HTTPS requests and forward them to your application. Your project already includes an `nginx.conf` file.
    *   You will need to configure NGINX to listen on ports 80 and 443 and proxy requests to the FastAPI container.

2.  **Configure DNS**:
    *   Update your domain's DNS records to point to the public IP address of your new Dell PowerEdge R430 server.
