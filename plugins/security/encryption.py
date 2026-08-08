"""
Encryption utilities for session data
"""

import os
import base64
from typing import Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from config import ENCRYPTION_KEY
from plugins.core.utils import get_logger

logger = get_logger(__name__)


class EncryptionManager:
    """Manage encryption/decryption of sensitive data"""
    
    def __init__(self):
        self.key = self._get_key()
        self.cipher = Fernet(self.key) if self.key else None
    
    def _get_key(self) -> Optional[bytes]:
        """Get encryption key from config or generate one"""
        if ENCRYPTION_KEY:
            try:
                # Test if key is directly valid for Fernet
                key = ENCRYPTION_KEY.encode()
                Fernet(key)
                return key
            except Exception:
                # If invalid, derive a proper 32-byte url-safe base64 key
                try:
                    salt = b'salt_'
                    kdf = PBKDF2HMAC(
                        algorithm=hashes.SHA256(),
                        length=32,
                        salt=salt,
                        iterations=100000,
                    )
                    key = base64.urlsafe_b64encode(kdf.derive(ENCRYPTION_KEY.encode()))
                    Fernet(key)
                    return key
                except Exception as e:
                    logger.error(f"Error deriving encryption key: {e}")
        
        # Generate a random key for development
        logger.warning("No encryption key set, generating random key")
        return Fernet.generate_key()
    
    def encrypt(self, data: str) -> Optional[str]:
        """Encrypt data"""
        if not self.cipher:
            logger.error("No encryption cipher available")
            return None
        
        try:
            encrypted = self.cipher.encrypt(data.encode())
            return base64.urlsafe_b64encode(encrypted).decode()
        except Exception as e:
            logger.error(f"Encryption error: {e}")
            return None
    
    def decrypt(self, encrypted_data: str) -> Optional[str]:
        """Decrypt data"""
        if not self.cipher:
            logger.error("No encryption cipher available")
            return None
        
        try:
            decoded = base64.urlsafe_b64decode(encrypted_data.encode())
            decrypted = self.cipher.decrypt(decoded)
            return decrypted.decode()
        except Exception as e:
            logger.error(f"Decryption error: {e}")
            return None


# Global encryption manager
encryption_manager = EncryptionManager()


def encrypt_data(data: str) -> Optional[str]:
    """Encrypt data (legacy function)"""
    return encryption_manager.encrypt(data)


def decrypt_data(encrypted_data: str) -> Optional[str]:
    """Decrypt data (legacy function)"""
    return encryption_manager.decrypt(encrypted_data)