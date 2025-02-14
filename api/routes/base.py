from fastapi import APIRouter, HTTPException
from typing import List

router = APIRouter()

@router.get("/",)
def get_all_items():
    return "Hey there 👋, you have found my all purpose api. You may or may have wanted to find this. Regardless, thanks for visiting. Go to https://www.adamsulemanji.com"
