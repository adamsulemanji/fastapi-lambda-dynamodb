from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import boto3
import jose
from jose import jwt
from jose.utils import base64url_decode
import json
import time
import logging

from core.aws_cognito import AWS_Cognito, AWS_COGNITO_USER_POOL_ID, AWS_REGION_NAME

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

security = HTTPBearer()


def get_aws_cognito():
    return AWS_Cognito()


_jwks_cache = None
_jwks_cache_timestamp = 0
_JWKS_CACHE_TIMEOUT = 300 

def get_cognito_jwks():
    global _jwks_cache, _jwks_cache_timestamp
    current_time = time.time()
    
    if _jwks_cache is None or (current_time - _jwks_cache_timestamp) > _JWKS_CACHE_TIMEOUT:
        logger.info("Refreshing Cognito JWKS cache")
        keys_url = f'https://cognito-idp.{AWS_REGION_NAME}.amazonaws.com/{AWS_COGNITO_USER_POOL_ID}/.well-known/jwks.json'
        
        try:
            import urllib.request
            with urllib.request.urlopen(keys_url) as f:
                response = f.read().decode('utf-8')
            _jwks_cache = json.loads(response)
            _jwks_cache_timestamp = current_time
        except Exception as e:
            logger.error(f"Failed to fetch JWKS: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Error fetching authentication information"
            )
    
    return _jwks_cache

def verify_token(token):
    # Get the JWKS from AWS Cognito
    jwks = get_cognito_jwks()
    
    # Get the header from the token
    try:
        header = jwt.get_unverified_header(token)
    except jose.exceptions.JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token header"
        )
    
    rsa_key = {}
    for key in jwks['keys']:
        if key['kid'] == header['kid']:
            rsa_key = {
                'kty': key['kty'],
                'kid': key['kid'],
                'use': key['use'],
                'n': key['n'],
                'e': key['e']
            }
            break
    
    if not rsa_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unable to find appropriate key"
        )
    
    try:
        payload = jwt.decode(
            token,
            rsa_key,
            algorithms=['RS256'],
            audience=None,
            options={
                'verify_exp': True,
                'verify_iat': True,
            }
        )
        
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired"
        )
    except jwt.JWTClaimsError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid claims"
        )
    except Exception as e:
        logger.error(f"Token validation error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token"
        )

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    payload = verify_token(token)
    
    
    return {
        "sub": payload["sub"],
        "email": payload.get("email", ""),
        "username": payload.get("cognito:username", ""),
        "groups": payload.get("cognito:groups", []),
        # Add any other relevant user information from the token
    }