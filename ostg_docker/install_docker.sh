#!/bin/bash

# OSTG Docker Installation Script
# This script installs Docker and sets up the FRR container environment

set -e

echo "=== OSTG Docker + FRR Installation ==="
echo "Installing Docker and setting up FRR container environment..."

# Function to check if running as root
check_root() {
    if [[ $EUID -ne 0 ]]; then
        echo "This script must be run as root (use sudo)"
        exit 1
    fi
}

# Function to detect OS
detect_os() {
    if [[ -f /etc/os-release ]]; then
        . /etc/os-release
        OS=$ID
        VERSION=$VERSION_ID
    else
        echo "Cannot detect OS. Please install Docker manually."
        exit 1
    fi
}

# Function to install Docker on Ubuntu/Debian
install_docker_ubuntu() {
    echo "Installing Docker on Ubuntu/Debian..."
    
    # Update package index
    apt-get update
    
    # Install prerequisites
    apt-get install -y \
        ca-certificates \
        curl \
        gnupg \
        lsb-release
    
    # Add Docker's official GPG key
    mkdir -p /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    
    # Set up the repository
    echo \
        "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
        $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
    
    # Update package index again
    apt-get update
    
    # Install Docker Engine
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    
    # Start and enable Docker
    systemctl start docker
    systemctl enable docker
    
    echo "Docker installed successfully on Ubuntu/Debian"
}

# Function to install Docker on CentOS/RHEL
install_docker_centos() {
    echo "Installing Docker on CentOS/RHEL..."
    
    # Install prerequisites
    yum install -y yum-utils
    
    # Add Docker repository
    yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
    
    # Install Docker Engine
    yum install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    
    # Start and enable Docker
    systemctl start docker
    systemctl enable docker
    
    echo "Docker installed successfully on CentOS/RHEL"
}

# Function to install Docker on Alpine
install_docker_alpine() {
    echo "Installing Docker on Alpine..."
    
    # Update package index
    apk update
    
    # Install Docker
    apk add docker docker-compose
    
    # Start and enable Docker
    rc-update add docker boot
    service docker start
    
    echo "Docker installed successfully on Alpine"
}

# Function to configure Docker for OSTG
configure_docker() {
    echo "Configuring Docker for OSTG..."
    
    # Create Docker group and add current user
    if ! getent group docker > /dev/null 2>&1; then
        groupadd docker
    fi
    
    # Add current user to docker group (if not root)
    if [[ $SUDO_USER ]]; then
        usermod -aG docker $SUDO_USER
        echo "Added $SUDO_USER to docker group"
    fi
    
    # Configure Docker daemon for privileged containers
    mkdir -p /etc/docker
    cat > /etc/docker/daemon.json << EOF
{
    "live-restore": true,
    "userland-proxy": false,
    "log-driver": "json-file",
    "log-opts": {
        "max-size": "10m",
        "max-file": "3"
    },
    "storage-driver": "overlay2"
}
EOF
    
    # Restart Docker to apply configuration
    systemctl restart docker
    
    echo "Docker configured for OSTG"
}

# Function to verify Docker installation
verify_docker() {
    echo "Verifying Docker installation..."
    
    # Check Docker version
    docker --version
    
    # Test Docker with hello-world
    docker run --rm hello-world
    
    echo "Docker verification successful"
}

# Function to build FRR Docker image
build_frr_image() {
    echo "Building FRR Docker image for OSTG..."
    
    # Check if we're in the OSTG directory
    if [[ ! -f "Dockerfile.frr" ]]; then
        echo "Error: Dockerfile.frr not found. Please run this script from the OSTG project directory."
        exit 1
    fi
    
    # Build the FRR image
    docker build -f Dockerfile.frr -t ostg-frr:latest .
    
    echo "FRR Docker image built successfully"
}

# Function to create OSTG Docker network
setup_ostg_network() {
    echo "Setting up OSTG Docker network..."
    
    # Create the OSTG FRR network
    docker network create --driver bridge ostg-frr-network 2>/dev/null || echo "Network ostg-frr-network already exists"
    
    echo "OSTG Docker network setup complete"
}

# Main installation function
main() {
    echo "Starting OSTG Docker + FRR installation..."
    
    # Check if running as root
    check_root
    
    # Detect OS
    detect_os
    echo "Detected OS: $OS $VERSION"
    
    # Install Docker based on OS
    case $OS in
        ubuntu|debian)
            install_docker_ubuntu
            ;;
        centos|rhel|fedora)
            install_docker_centos
            ;;
        alpine)
            install_docker_alpine
            ;;
        *)
            echo "Unsupported OS: $OS"
            echo "Please install Docker manually and run this script again with --skip-docker"
            exit 1
            ;;
    esac
    
    # Configure Docker
    configure_docker
    
    # Verify installation
    verify_docker
    
    # Build FRR image
    build_frr_image
    
    # Setup network
    setup_ostg_network
    
    echo ""
    echo "=== OSTG Docker + FRR Installation Complete ==="
    echo ""
    echo "Docker has been installed and configured for OSTG."
    echo "FRR Docker image has been built and is ready to use."
    echo ""
    echo "To use Docker without sudo, log out and log back in, or run:"
    echo "  newgrp docker"
    echo ""
    echo "You can now start the OSTG server with FRR Docker support."
}

# Handle command line arguments
case "${1:-}" in
    --skip-docker)
        echo "Skipping Docker installation..."
        verify_docker
        build_frr_image
        setup_ostg_network
        ;;
    --help|-h)
        echo "Usage: $0 [--skip-docker] [--help]"
        echo "  --skip-docker  Skip Docker installation, only build FRR image"
        echo "  --help         Show this help message"
        exit 0
        ;;
    *)
        main
        ;;
esac
