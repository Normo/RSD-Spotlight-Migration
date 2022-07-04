#!/usr/bin/env python3

"""
Migration script for the spotlights from the hifis.net website.
"""

import os
import re
import glob
import logging

import yaml
import jwt

import asyncio
from postgrest import AsyncPostgrestClient

DEBUG = True
POSTGREST_URL = os.environ.get("POSTGREST_URL")
PGRST_JWT_SECRET = os.environ.get("PGRST_JWT_SECRET")
JWT_PAYLOAD = {"role": "rsd_admin"}
JWT_ALGORITHM = "HS256"
SPOTLIGHTS_DIR = "hifis.net/_spotlights"


def get_md_without_front_matter(file):
    found = 0
    retlines = []

    with open(file, "r") as opened_file:
        alllines = opened_file.readlines()

        for line in alllines:
            if line.startswith("---"):
                found += 1

            if found >= 2:
                retlines.append(line)

    return "\n".join(retlines)


def get_spotlights():
    files = glob.glob(SPOTLIGHTS_DIR + os.sep + "*.md")
    filtered = list(filter(lambda x: "_template.md" not in x, files))

    spotlights = []

    for file in filtered:
        with open(file, "r") as opened_file:
            try:
                # https://stackoverflow.com/a/34727830
                load_all = yaml.load_all(opened_file, Loader=yaml.FullLoader)

                metadata = next(load_all)
                metadata["description"] = get_md_without_front_matter(file)

                if metadata.get("name") is None:
                    raise Exception("Spotlight %s has no name" % file)

                spotlights.append(metadata)
            except yaml.YAMLError as exc:
                print(exc)

    return spotlights


def name_to_slug(name):
    remove_chars = name.replace(" ", "-").replace("+", "").lower()

    # remove multiple '-'
    return re.sub(r"\-+", "-", remove_chars)


def org_name_to_slug(name):
    return (
        name.replace("(", "")
        .replace(")", "")
        .replace(" ", "-")
        .replace("+", "")
        .lower()
    )


async def slug_to_id(client, slug):
    res = (
        await client.from_("software")
        .select("id", "slug")
        .eq("slug", slug)
        .execute()
    )

    if len(res.data) > 0:
        return res.data[0].get("id")

    return None


def convert_spotlight_to_software(spotlight):
    name = spotlight.get("name")

    return {
        "slug": name_to_slug(name),
        "brand_name": name,
        "is_published": True,
        "short_statement": spotlight.get("excerpt", "")[:300],
        "description": spotlight.get("description", ""),
    }


async def remove_spotlight(client, spotlight):
    name = spotlight.get("name")
    slug = name_to_slug(name)
    software_id = await slug_to_id(client, slug)

    if software_id is not None:
        # remove related entries
        res = (
            await client.from_("repository_url")
            .delete()
            .eq("software", software_id)
            .execute()
        )

        res = (
            await client.from_("license_for_software")
            .delete()
            .eq("software", software_id)
            .execute()
        )

        res = (
            await client.from_("contributor")
            .delete()
            .eq("software", software_id)
            .execute()
        )

        res = (
            await client.from_("keyword_for_software")
            .delete()
            .eq("software", software_id)
            .execute()
        )

        res = (
            await client.from_("software_for_organisation")
            .delete()
            .eq("software", software_id)
            .execute()
        )

    logging.info("Remove %s", name)

    res = await client.from_("software").delete().eq("slug", slug).execute()

    logging.info(res.data)


async def add_spotlight(client, spotlight):
    name = spotlight.get("name")

    logging.info("Add %s", name)

    sw_data = convert_spotlight_to_software(spotlight)

    logging.info(sw_data)

    res = await client.from_("software").insert(sw_data).execute()

    logging.info(res.data)


async def add_repository_url(client, spotlight):
    name = spotlight.get("name")
    slug = name_to_slug(name)

    platforms = spotlight.get("platforms", [])

    if len(platforms) == 0:
        logging.info("Spotlight %s has no platforms", name)
        return

    found_github = None
    found_gitlab = None

    for plat in platforms:
        ptype = plat.get("type")

        if ptype == "gitlab":
            found_gitlab = plat.get("link_as")
        if ptype == "github":
            found_github = plat.get("link_as")

    software_id = await slug_to_id(client, slug)

    # first check for GitLab as we prefer it over GitHub and only one entry
    # can be made in the RSD
    if found_gitlab is not None:
        to_add = {
            "software": software_id,
            "code_platform": "gitlab",
            "url": found_gitlab,
        }
    elif found_github is not None:
        to_add = {
            "software": software_id,
            "code_platform": "github",
            "url": found_github,
        }
    else:
        # unsupported type -> nothing to add
        return

    logging.info("Add repository URL for %s", name)

    res = await client.from_("repository_url").insert(to_add).execute()

    logging.info(res.data)


async def add_license(client, spotlight):
    slicense = spotlight.get("license")

    if slicense is None or len(slicense) == 0:
        # no license specified
        return

    name = spotlight.get("name")
    slug = name_to_slug(name)
    software_id = await slug_to_id(client, slug)

    logging.info("Add license for %s", name)

    res = (
        await client.from_("license_for_software")
        .insert({"software": software_id, "license": slicense})
        .execute()
    )

    logging.info(res.data)


async def get_id_for_keyword(client, keyword):
    res = (
        await client.from_("keyword")
        .select("id", "value")
        .eq("value", keyword)
        .execute()
    )

    if len(res.data) > 0:
        return res.data[0].get("id")

    return None


async def add_keywords(client, spotlight):
    keywords = spotlight.get("keywords")

    if keywords is None or len(keywords) == 0:
        # no keyword specified
        return

    name = spotlight.get("name")
    slug = name_to_slug(name)
    software_id = await slug_to_id(client, slug)

    logging.info("Add keywords for %s", name)

    for keyword in keywords:
        kw_id = await get_id_for_keyword(client, keyword)

        if kw_id is None:
            logging.info("Adding keyword %s" % keyword)

            res = (
                await client.from_("keyword")
                .insert({"value": keyword})
                .execute()
            )

            logging.info(res.data)

            kw_id = await get_id_for_keyword(client, keyword)

        res = (
            await client.from_("keyword_for_software")
            .insert({"software": software_id, "keyword": kw_id})
            .execute()
        )

        logging.info(res.data)


async def get_id_for_organisation(client, org):
    res = (
        await client.from_("organisation")
        .select("id", "name")
        .eq("name", org)
        .execute()
    )

    if len(res.data) > 0:
        return res.data[0].get("id")

    return None


async def add_organisations(client, spotlight):
    orgs = spotlight.get("hgf_centers")

    if orgs is None or len(orgs) == 0:
        # no organisation specified
        return

    name = spotlight.get("name")
    slug = name_to_slug(name)
    software_id = await slug_to_id(client, slug)

    logging.info("Add organisations for %s", name)

    for org in orgs:
        org_id = await get_id_for_organisation(client, org)

        if org_id is None:
            logging.info("Adding organisation %s" % org)

            org_slug = org_name_to_slug(org)

            res = (
                await client.from_("organisation")
                .insert({"name": org, "slug": org_slug})
                .execute()
            )

            logging.info(res.data)

            org_id = await get_id_for_organisation(client, org)

        logging.info("Adding organisation %s to software %s" % (org, name))

        res = (
            await client.from_("software_for_organisation")
            .insert({"software": software_id, "organisation": org_id})
            .execute()
        )

        logging.info(res.data)


async def main():
    token = jwt.encode(JWT_PAYLOAD, PGRST_JWT_SECRET, algorithm=JWT_ALGORITHM)
    spotlights = get_spotlights()

    async with AsyncPostgrestClient(POSTGREST_URL) as client:
        client.auth(token=token)

        for spot in spotlights:
            # update existing -> remove first
            await remove_spotlight(client, spot)
            await add_spotlight(client, spot)
            await add_repository_url(client, spot)
            await add_license(client, spot)
            await add_keywords(client, spot)
            await add_organisations(client, spot)


if __name__ == "__main__":
    if DEBUG:
        logging.basicConfig(level=logging.INFO)
    else:
        logging.basicConfig(level=logging.WARN)

    asyncio.run(main())