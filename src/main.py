import os
import json
import argparse
import logging
from datetime import datetime
from typing import Dict, List
import requests
from dotenv import load_dotenv
import time

# Load environment variables from .env file
load_dotenv()

# Configure logging
log_dir = "logs"
os.makedirs(log_dir, exist_ok=True)
timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
log_filename = os.path.join(log_dir, f"user_migration_{timestamp}.log")

logging.basicConfig(
    filename=log_filename,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

class KeycloakMigrationTool:
    def __init__(self, path: str, realm: str):
        self.path = path
        self.realm = realm
        self.project_id = os.getenv('DESCOPE_PROJECT_ID')
        self.management_key = os.getenv('DESCOPE_MANAGEMENT_KEY')

        if not self.project_id or not self.management_key:
            raise ValueError("Environment variables DESCOPE_PROJECT_ID and DESCOPE_MANAGEMENT_KEY must be set.")

   
    def process_files(self) -> None:
        """Process all user export files in the specified directory that match the realm"""
        try:
            file_pattern = f"{self.realm}-users-"
            user_count = 0
            for file_name in os.listdir(self.path):
                if file_name.startswith(file_pattern) and file_name.endswith('.json'):
                    file_path = os.path.join(self.path, file_name)
                    with open(file_path, 'r') as f:
                        file_data = json.load(f)
                    
                    if isinstance(file_data, dict) and "users" in file_data:
                        users_data = file_data["users"]
                        num_users = self.batch_create_users(users_data)
                        user_count += num_users
                        if user_count % 10 == 0:
                                print(f"Processed {user_count} users...")
                        # time.sleep()
                    else:
                        logging.error(f"Invalid file format in {file_path}: missing 'users' array")
        except Exception as e:
            logging.error(f"Failed to process files in {self.path}: {str(e)}")

    def batch_create_users(self, users_data: List[Dict]) -> int:
        user_batch = []
        try: 
            for user_data in users_data:
                email = user_data.get("email")
                username = user_data.get("username")
                verified_email = user_data.get("emailVerified", False)
                
                # Determine loginId and additionalIdentifiers
                login_id = username if username else email
                additional_identifiers = [email] if username else []

                # Prepare hashedPassword
                credentials = user_data.get("credentials", [])
                hashed_password = None
                for credential in credentials:
                    if credential.get("type") == "password":
                        secret_data = json.loads(credential.get("secretData", "{}"))
                        cred_data = json.loads(credential.get("credentialData", "{}"))
                        hashed_password = {
                            "argon2": {
                                "hash": secret_data.get("value", ""),
                                "salt": secret_data.get("salt", ""),
                                "iterations": cred_data.get("hashIterations", 3),
                                "memory": int(cred_data.get("additionalParameters", {}).get("memory", ["7168"])[0]),
                                "threads": int(cred_data.get("additionalParameters", {}).get("parallelism", ["1"])[0])
                            }
                        }
                        break

                # Prepare payload
                user = {
                    "loginId": login_id,
                    "email": email,
                    "verifiedEmail": verified_email,
                    "additionalIdentifiers": additional_identifiers,
                    "hashedPassword": hashed_password
                }

                user_batch.append(user)

            # Prepare payload
            payload = {
                "users": user_batch,
                "invite": False,
                "sendMail": False,
                "sendSMS": False,
            }

            # API request
            url = "https://api.descope.com/v1/mgmt/user/create/batch"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.project_id}:{self.management_key}"
            }

            response = requests.post(url, headers=headers, json=payload)

            num_users = len(user_batch)

            if response.status_code == 200:
                logging.info(f"Successfully created {num_users} users")
            else:
                logging.error(f"Failed to create {num_users} users: {response.status_code} - {response.text}")

            return num_users

        except Exception as e:
            logging.error(f"Failed to create {num_users} users: {str(e)}")
    
def main():
    parser = argparse.ArgumentParser(description='Create users in Descope from Keycloak export files')
    parser.add_argument('--path', required=True, help='Path to the exported users folder')
    parser.add_argument('--realm', required=True, help='Name of the Keycloak realm')
    args = parser.parse_args()

    migration_tool = KeycloakMigrationTool(args.path, args.realm)
    migration_tool.process_files()

if __name__ == "__main__":
    main() 