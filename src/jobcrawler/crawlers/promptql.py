
import html
import json
import re

SITE = "https://jobs.gem.com/api/public/graphql"
EXTRA_HOSTS = []

BOARD_ID = "promptql"
COMPANY = "PromptQL"


def _strip_html(value):
    if not value:
        return None
    text = value
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|tr|h[1-6])\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li[^>]*>", "\n- ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _post(ctx, query, variables):
    return json.loads(ctx.fetch(SITE, method="POST", json={"query": query, "variables": variables}))


def _locations(posting):
    values = []
    for loc in posting.get("locations") or []:
        name = loc.get("name")
        if name:
            values.append(name)
    return values


def _put(job, key, value):
    if value is not None and value != "":
        job[key] = value


def crawl(ctx):
    list_query = """
    query JobBoardList($boardId: String!) {
      oatsExternalJobPostings(boardId: $boardId) {
        jobPostings {
          id
          extId
          title
          locations { name city isoCountry isRemote }
          job {
            department { name }
            locationType
            employmentType
          }
        }
      }
      jobBoardExternal(vanityUrlPath: $boardId) {
        teamDisplayName
        pageTitle
      }
    }
    """
    detail_query = """
    query JobBoardDetail($boardId: String!, $extId: String!) {
      oatsExternalJobPosting(boardId: $boardId, extId: $extId) {
        id
        extId
        title
        descriptionHtml
        locations { name city isoCountry isRemote }
        job {
          department { name }
          locationType
          employmentType
        }
      }
    }
    """

    board = _post(ctx, list_query, {"boardId": BOARD_ID})
    postings = (((board.get("data") or {}).get("oatsExternalJobPostings") or {}).get("jobPostings") or [])

    for listed in postings:
        ext_id = listed.get("extId")
        if not ext_id:
            continue

        detail = _post(ctx, detail_query, {"boardId": BOARD_ID, "extId": ext_id})
        posting = ((detail.get("data") or {}).get("oatsExternalJobPosting") or listed)
        job_info = posting.get("job") or {}
        department = (job_info.get("department") or {}).get("name")
        locations = _locations(posting)
        location = locations[0] if locations else None
        description = _strip_html(posting.get("descriptionHtml"))
        title = (posting.get("title") or "").strip()

        if not (title and location and description):
            continue

        item = {
            "url": "https://jobs.gem.com/promptql/%s" % ext_id,
            "title": title,
            "company": COMPANY,
            "location": location,
            "description": description,
            "source": SITE,
        }
        _put(item, "locations", locations)
        _put(item, "external_id", ext_id)
        _put(item, "department", department)
        _put(item, "employment_type", job_info.get("employmentType"))
        _put(item, "workplace_type", job_info.get("locationType"))
        _put(item, "apply_url", "https://jobs.gem.com/promptql/%s/application" % ext_id)
        yield item
