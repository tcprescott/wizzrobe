"""
Emergency Contact Service - Business Logic Layer

A member gives one community the person to call if something happens to them at
an event; that community's STAFF can open it when they need it.

Who sees what is the whole design. The member sees and edits their own contact,
and when staff last opened it. STAFF (and super-admins, via
``AuthService.is_staff``) can open one member's contact at a time, and every
open writes an audit row, whether or not a contact is on file. Nobody else sees
anything: no list of contacts, no export, nothing on the event bus, no REST or
MCP surface, and audit rows carry ids only.

Gated by :attr:`~models.FeatureFlag.EMERGENCY_CONTACTS`.
"""

import re
from datetime import datetime, timezone
from typing import Optional

from application.feature_flags import requires_feature
from application.repositories.emergency_contact_repository import EmergencyContactRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.tenant_membership_service import TenantMembershipService
from models import EmergencyContact, FeatureFlag, User

NAME_MAX_LENGTH = 100
RELATIONSHIP_MAX_LENGTH = 60
PHONE_MAX_LENGTH = 40

_PHONE_ALLOWED = re.compile(r'^[0-9+()\-./ x#]*$', re.IGNORECASE)
_MIN_PHONE_DIGITS = 5


def _clean(text: Optional[str], limit: int, what: str) -> Optional[str]:
    text = ' '.join((text or '').split())
    if len(text) > limit:
        raise ValueError(f'{what} must be {limit} characters or fewer.')
    return text or None


def _clean_phone(phone: Optional[str]) -> str:
    phone = _clean(phone, PHONE_MAX_LENGTH, 'Phone number')
    if phone is None:
        raise ValueError('Add a phone number for your emergency contact.')
    if not _PHONE_ALLOWED.match(phone) or sum(c.isdigit() for c in phone) < _MIN_PHONE_DIGITS:
        raise ValueError('Enter a phone number using digits, spaces, + ( ) and -.')
    return phone


class EmergencyContactService:
    """Service for members' emergency contacts."""

    def __init__(self) -> None:
        self.repository = EmergencyContactRepository()
        self.audit_service = AuditService()

    @requires_feature(FeatureFlag.EMERGENCY_CONTACTS)
    async def get_mine(self, actor: User) -> Optional[EmergencyContact]:
        """``actor``'s own contact in this community, with who last opened it."""
        return await self.repository.get_for_user(actor.id)

    @requires_feature(FeatureFlag.EMERGENCY_CONTACTS)
    async def save_mine(
        self, actor: User, name: Optional[str], relationship: Optional[str], phone: Optional[str],
    ) -> EmergencyContact:
        """Add or change ``actor``'s contact. Re-saving the same values is a no-op."""
        name = _clean(name, NAME_MAX_LENGTH, 'Name')
        if name is None:
            raise ValueError("Add your emergency contact's name.")
        relationship = _clean(relationship, RELATIONSHIP_MAX_LENGTH, 'Relationship')
        phone = _clean_phone(phone)

        existing = await self.repository.get_for_user(actor.id)
        if existing is None:
            if not await TenantMembershipService().is_member(actor):
                raise ValueError('Join this community before adding an emergency contact.')
            contact = await self.repository.create_for(
                actor, name=name, relationship=relationship, phone=phone,
            )
        elif (existing.name, existing.relationship, existing.phone) == (name, relationship, phone):
            return existing
        else:
            contact = await self.repository.update(
                existing, name=name, relationship=relationship, phone=phone,
            )
        await self._audit(actor, AuditActions.EMERGENCY_CONTACT_SAVED, contact.user_id, contact.id)
        return contact

    @requires_feature(FeatureFlag.EMERGENCY_CONTACTS)
    async def remove_mine(self, actor: User) -> None:
        """Delete ``actor``'s contact outright. Nothing of it is kept."""
        existing = await self.repository.get_for_user(actor.id)
        if existing is None:
            return
        contact_id = existing.id
        await self.repository.delete(existing)
        await self._audit(actor, AuditActions.EMERGENCY_CONTACT_REMOVED, actor.id, contact_id)

    # feature-gate: exempt — soft integration point called from removing a member.
    async def remove_for_removed_member(self, actor: User, member: User) -> None:
        """Delete a removed member's contact.

        Staff must not keep a phone number for someone who is no longer in the
        community. Runs whatever the flag says: it only ever removes data.
        """
        existing = await self.repository.get_for_user(member.id)
        if existing is None:
            return
        contact_id = existing.id
        await self.repository.delete(existing)
        await self._audit(
            actor, AuditActions.EMERGENCY_CONTACT_REMOVED, member.id, contact_id,
            source='member_removed',
        )

    @requires_feature(FeatureFlag.EMERGENCY_CONTACTS)
    async def open_for(self, actor: User, member: User) -> Optional[EmergencyContact]:
        """Show staff one member's contact, logging the look.

        The audit row is written before anything is returned, and a look at a
        member with no contact is logged too: the log answers "who went
        looking", not only "who found something". The member's profile shows
        the latest look.
        """
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            'Only staff can open emergency contacts.',
        )
        contact = await self.repository.get_for_user(member.id)
        await self._audit(
            actor, AuditActions.EMERGENCY_CONTACT_VIEWED, member.id,
            contact.id if contact else None,
        )
        if contact is not None:
            await self.repository.update(
                contact, last_viewed_at=datetime.now(timezone.utc), last_viewed_by=actor,
            )
        return contact

    async def _audit(
        self, actor: User, action: str, user_id: int, contact_id: Optional[int], **extra: str,
    ) -> None:
        await self.audit_service.write_log(
            actor,
            action,
            {'user_id': user_id, 'contact_id': contact_id, **extra},
        )
