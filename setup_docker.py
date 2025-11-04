#!/usr/bin/env python3
"""
OSTG Docker Setup Module
Handles Docker installation and FRR container setup as part of OSTG package installation
"""

import os
import sys
import subprocess
import platform
import shutil
from pathlib import Path

class OSTGDockerInstaller:
    """Handles Docker installation and FRR setup for OSTG"""
    
    def __init__(self, project_root=None):
        self.project_root = project_root or Path(__file__).parent
        
        # Try to find Docker files in package location first
        try:
            import pkg_resources
            docker_package_path = Path(pkg_resources.get_distribution('ostg-trafficgen').location) / 'ostg_docker'
            if docker_package_path.exists():
                self.docker_script = docker_package_path / "install_docker.sh"
                self.dockerfile_frr = docker_package_path / "Dockerfile.frr"
                self.start_frr_script = docker_package_path / "start-frr.sh"
                self.frr_config_template = docker_package_path / "frr.conf.template"
            else:
                # Fallback to project root
                self.docker_script = self.project_root / "install_docker.sh"
                self.dockerfile_frr = self.project_root / "Dockerfile.frr"
                self.start_frr_script = self.project_root / "start-frr.sh"
                self.frr_config_template = self.project_root / "frr.conf.template"
        except:
            # Fallback to project root
            self.docker_script = self.project_root / "install_docker.sh"
            self.dockerfile_frr = self.project_root / "Dockerfile.frr"
            self.start_frr_script = self.project_root / "start-frr.sh"
            self.frr_config_template = self.project_root / "frr.conf.template"
        
    def check_docker_installed(self):
        """Check if Docker is already installed"""
        try:
            result = subprocess.run(['docker', '--version'], 
                                  capture_output=True, text=True, check=True)
            print(f"Docker is already installed: {result.stdout.strip()}")
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False
    
    def check_docker_running(self):
        """Check if Docker daemon is running"""
        try:
            subprocess.run(['docker', 'info'], 
                         capture_output=True, text=True, check=True)
            return True
        except subprocess.CalledProcessError:
            return False
    
    def check_frr_image_exists(self):
        """Check if OSTG FRR Docker image exists"""
        try:
            result = subprocess.run(['docker', 'images', 'ostg-frr:latest'], 
                                  capture_output=True, text=True, check=True)
            return 'ostg-frr' in result.stdout
        except subprocess.CalledProcessError:
            return False
    
    def check_required_files(self):
        """Check if all required files for FRR Docker setup exist"""
        required_files = [
            self.dockerfile_frr,
            self.start_frr_script,
            self.frr_config_template
        ]
        
        missing_files = []
        for file_path in required_files:
            if not file_path.exists():
                missing_files.append(str(file_path))
        
        if missing_files:
            print(f"Missing required files: {', '.join(missing_files)}")
            return False
        
        return True
    
    def install_docker(self, skip_if_installed=True):
        """Install Docker using the install_docker.sh script"""
        if skip_if_installed and self.check_docker_installed():
            print("Docker is already installed, skipping installation")
            return True
        
        if not self.docker_script.exists():
            print(f"Error: Docker installation script not found at {self.docker_script}")
            return False
        
        print("Installing Docker...")
        try:
            # Make script executable
            os.chmod(self.docker_script, 0o755)
            
            # Run the installation script
            result = subprocess.run([str(self.docker_script)], 
                                  cwd=self.project_root, check=True)
            print("Docker installation completed successfully")
            return True
            
        except subprocess.CalledProcessError as e:
            print(f"Error installing Docker: {e}")
            return False
    
    def build_frr_image(self):
        """Build the OSTG FRR Docker image"""
        if not self.check_required_files():
            print("Cannot build FRR image: required files missing")
            return False
        
        if self.check_frr_image_exists():
            print("OSTG FRR Docker image already exists")
            return True
        
        print("Building OSTG FRR Docker image...")
        try:
            result = subprocess.run([
                'docker', 'build', '-f', 'Dockerfile.frr', 
                '-t', 'ostg-frr:latest', '.'
            ], cwd=self.project_root, check=True)
            
            print("OSTG FRR Docker image built successfully")
            return True
            
        except subprocess.CalledProcessError as e:
            print(f"Error building FRR image: {e}")
            return False
    
    def setup_docker_network(self):
        """Create the OSTG Docker network"""
        print("Setting up OSTG Docker network...")
        try:
            # Try to create the network (ignore if it already exists)
            subprocess.run([
                'docker', 'network', 'create', '--driver', 'bridge', 
                'ostg-frr-network'
            ], capture_output=True)
            
            print("OSTG Docker network setup complete")
            return True
            
        except subprocess.CalledProcessError as e:
            print(f"Error setting up Docker network: {e}")
            return False
    
    def verify_installation(self):
        """Verify that Docker and FRR setup is working correctly"""
        print("Verifying OSTG Docker + FRR installation...")
        
        checks = [
            ("Docker installed", self.check_docker_installed),
            ("Docker running", self.check_docker_running),
            ("FRR image exists", self.check_frr_image_exists),
        ]
        
        all_passed = True
        for check_name, check_func in checks:
            if check_func():
                print(f"✓ {check_name}")
            else:
                print(f"✗ {check_name}")
                all_passed = False
        
        if all_passed:
            print("✓ OSTG Docker + FRR installation verified successfully")
        else:
            print("✗ Some verification checks failed")
        
        return all_passed
    
    def install_all(self, install_docker=True):
        """Complete installation of Docker + FRR for OSTG"""
        print("=== OSTG Docker + FRR Installation ===")
        
        success = True
        
        # Install Docker if requested
        if install_docker:
            if not self.install_docker():
                success = False
        
        # Build FRR image
        if not self.build_frr_image():
            success = False
        
        # Setup Docker network
        if not self.setup_docker_network():
            success = False
        
        # Verify installation
        if not self.verify_installation():
            success = False
        
        if success:
            print("\n=== Installation Complete ===")
            print("OSTG Docker + FRR setup is ready!")
            print("You can now use FRR Docker containers with your OSTG traffic generator.")
        else:
            print("\n=== Installation Failed ===")
            print("Some components failed to install. Please check the errors above.")
        
        return success

def main():
    """Main function for command-line usage"""
    import argparse
    
    parser = argparse.ArgumentParser(description='OSTG Docker + FRR Installer')
    parser.add_argument('--skip-docker', action='store_true',
                       help='Skip Docker installation, only setup FRR')
    parser.add_argument('--verify-only', action='store_true',
                       help='Only verify existing installation')
    parser.add_argument('--project-root', type=str,
                       help='Path to OSTG project root directory')
    
    args = parser.parse_args()
    
    installer = OSTGDockerInstaller(args.project_root)
    
    if args.verify_only:
        installer.verify_installation()
    else:
        installer.install_all(install_docker=not args.skip_docker)

if __name__ == '__main__':
    main()
