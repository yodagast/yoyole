"""用户收货地址路由：CRUD + 默认地址"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_customer
from app.models import Address, Customer
from app.schemas import AddressIn, AddressOut, AddressUpdateIn

router = APIRouter(prefix="/api/addresses", tags=["addresses"])


@router.get("", response_model=list[AddressOut])
async def list_addresses(
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    """地址列表（默认地址在前）"""
    result = await db.execute(
        select(Address)
        .where(Address.customer_id == customer.id)
        .order_by(Address.is_default.desc(), Address.id.desc())
    )
    return result.scalars().all()


@router.post("", response_model=AddressOut, status_code=201)
async def create_address(
    payload: AddressIn,
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    """新增地址，若设为默认则取消其他默认"""
    if payload.is_default:
        await _unset_default(customer.id, db)

    addr = Address(
        customer_id=customer.id,
        receiver_name=payload.receiver_name,
        receiver_phone=payload.receiver_phone,
        detail=payload.detail,
        is_default=payload.is_default,
    )
    db.add(addr)
    await db.commit()
    await db.refresh(addr)
    return addr


@router.put("/{address_id}", response_model=AddressOut)
async def update_address(
    address_id: int,
    payload: AddressUpdateIn,
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    """更新地址"""
    result = await db.execute(
        select(Address).where(Address.id == address_id, Address.customer_id == customer.id)
    )
    addr = result.scalar_one_or_none()
    if not addr:
        raise HTTPException(status_code=404, detail="地址不存在")

    if payload.is_default:
        await _unset_default(customer.id, db)

    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(addr, field, value)

    await db.commit()
    await db.refresh(addr)
    return addr


@router.delete("/{address_id}")
async def delete_address(
    address_id: int,
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    """删除地址"""
    result = await db.execute(
        select(Address).where(Address.id == address_id, Address.customer_id == customer.id)
    )
    addr = result.scalar_one_or_none()
    if not addr:
        raise HTTPException(status_code=404, detail="地址不存在")
    await db.delete(addr)
    await db.commit()
    return {"message": "已删除"}


async def _unset_default(customer_id: int, db: AsyncSession) -> None:
    """取消该客户所有地址的默认标记"""
    result = await db.execute(
        select(Address).where(
            Address.customer_id == customer_id, Address.is_default.is_(True)
        )
    )
    for addr in result.scalars().all():
        addr.is_default = False