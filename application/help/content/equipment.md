---
title: Borrowing equipment
slug: equipment
icon: inventory_2
order: 65
feature: EQUIPMENT
summary: Borrowing loaner gear by scanning its QR code, giving it back, and running the equipment desk.
---

Some communities lend out gear at their events, like controllers, adapters, cables
and headsets. Every item has a sticker with a QR code and a number, and the app
keeps track of who has what.

## Borrowing something

:::snippet equipment-borrow
Scan the QR code on the item with your phone's camera. It opens that item's page in
the app. If you aren't signed in, you'll sign in with Discord first and come back
to the item.

Tap **Check out to me** and it's yours. It's recorded against your name until it
comes back, and anyone who opens its page can see you have it.

You don't need a role to borrow. Anyone signed in to the community can check gear
out to themselves.

You won't see the button in these cases.

| You see | Why |
|---|---|
| **Checked out** and someone's name | Someone else has it. Find them, or ask the equipment desk for another one. |
| **Retired** | It's been taken out of use. Ask the desk for a replacement. |
:::

The item's page also shows who owns it (a person, or the community), a short
description, and its loan history.

## Giving it back

:::snippet equipment-return
Hand it to the equipment desk or any staff member, and they'll record the return.

You can't check an item back in yourself, and that's deliberate. Someone who can
see the item is actually back records the return, so nobody gets blamed for a
cable that's still sitting under a setup. If you open the page of something you're
holding, it tells you who to give it to.
:::

![An item's page on a phone, checked out to you, saying returns are recorded by staff or an equipment manager, with its loan history](/static/help/equipment/held-phone.webp "What an item you're holding looks like.")

The item stays against your name until it's checked in. If you've returned
something and it still shows as yours, tell the desk.

## Seeing what you have out

:::snippet equipment-yours
While you're holding something, the top of your Profile has an Equipment you
have out card listing everything checked out to you, with when you took it.
**View** opens the item's page.

The card goes away once everything you borrowed is checked back in, so it's the
quickest way to make sure you've returned it all before you leave.
:::

![The Equipment you have out card on Profile, with one item and its View button marked 1](/static/help/equipment/yours-phone.webp "Equipment you have out, on Profile. **View** (1) opens the item.")

## Running the equipment desk

This part's for equipment managers and staff, the people who hand gear out and
take it back. Everyone else can stop here.

:::snippet equipment-desk
Your register is Admin → Equipment. It lists every item with its number, owner,
status and who has it. Search filters as you type, which is the fastest way to find
an item by number or a person by name.

Each row has one button for whatever the item needs next: **Check out** when it's
available, or **Check in** when someone has it. Everything else is in the ⋮ menu at
the end of the row.

| Menu entry | Does |
|---|---|
| **Open asset page** | Opens the item's own page, the same one its QR code goes to. |
| **Edit** | Changes its name, description, owner, notes or status. |
| **Delete** | Removes the item for good. It asks first. |

**Check out** lends the item to someone (pick the borrower from the list), and
**Check in** records it as returned so it's available again right away. On a phone
the list becomes cards with the same button and menu.

Scanning an item's QR code works for you too, and it's often quicker at a busy
desk. You get **Check out…** (with the borrower picker) instead of *Check out to
me*, and **Check in** on anything that's out.
:::

![The Admin → Equipment register, listing each item with its owner, status and holder; the first row's Check in button is marked 1, the second row's Check out button 2, and the first row's ⋮ menu 3](/static/help/equipment/desk-desktop.webp "The equipment register: **Check in** (1) or **Check out** (2), and everything else in the ⋮ menu (3).")

### Lending to someone

The borrower picker (1) starts on you and lists the people in this community. Type
to search. If the person isn't there, they probably haven't joined the community in
the app yet. Tick **Include people outside this community** (2) to search every
account in the app. A borrower still needs an account, so someone who's never
signed in has to do that first.

![The Check out equipment dialog, with the Borrower picker marked 1 and the Include people outside this community box marked 2](/static/help/equipment/checkout-desktop.webp "Lending an item to someone.")

### Adding gear

**Add asset** creates a new item. Numbers are handed out automatically, in order,
so you never pick one.

Only the name is required. Leave the owner as the community unless the gear belongs
to a person. Set it for anything someone lent the event, so it's clear whose it is
at the end. Private notes are only shown to equipment managers and staff, for
things like a serial number or "left stick drifts".

**Quantity** creates that many identical items at once, up to 200, with
consecutive numbers. Ten of the same controller is one save.

### Printing labels

**Actions → Print QR labels** lets you pick which items to print, then opens a
printable sheet in a new tab. Select them by hand, or narrow the list to available
items, one owner, or a range of numbers.

The plain grid prints on Letter or A4 with cut lines. The Avery templates (5160,
5162 and 5163) line up with those peel-off sticker sheets. For those, print at
**100% (Actual size)** with margins set to **None**, or the labels drift off the
stickers. Each label can also show the owner, the community name and the
description.

An item's own page has **Print label** and **Download QR** buttons, for replacing
one worn sticker.

### Retiring an item

To take something out of use, edit it and set its status to **Retired**. It stays in
the register with its history, and nobody can check it out. Set it back to
*Available to lend* once it's fixed.

You can't retire or delete an item that's checked out. Check it in first.

> Retire things instead of deleting them. Deleting an item also deletes its loan
> history, so you lose the record of who had it and when.
