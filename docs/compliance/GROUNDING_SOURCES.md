# Compliance grounding sources

Authoritative regulatory sources the application's compliance work grounds in. **Read this before writing any compliance-related code or claim.** AI training data is not a reliable source for regulatory text — these are.

## Federal frameworks

### FERPA — Family Educational Rights and Privacy Act

**Statute:** 20 U.S.C. § 1232g
**Regulations:** 34 CFR Part 99
**Enforcement:** U.S. Department of Education, Student Privacy Policy Office (SPPO)
**Plain-language guidance:** https://studentprivacy.ed.gov/

Authoritative URLs:

- Statute: https://www.law.cornell.edu/uscode/text/20/1232g
- Regulations: https://www.ecfr.gov/current/title-34/subtitle-A/part-99
- DOE Student Privacy Policy Office: https://studentprivacy.ed.gov/
- "School Officials" exception guidance: https://studentprivacy.ed.gov/faq/what-are-school-officials-who-have-legitimate-educational-interests

### IDEA — Individuals with Disabilities Education Act

**Statute:** 20 U.S.C. § 1400 et seq. (Part B is the K-12 special education portion)
**Regulations:** 34 CFR Part 300
**Enforcement:** U.S. Department of Education, Office of Special Education Programs (OSEP)

Authoritative URLs:

- Statute: https://www.law.cornell.edu/uscode/text/20/chapter-33
- Regulations: https://www.ecfr.gov/current/title-34/subtitle-B/chapter-III/part-300
- OSEP: https://sites.ed.gov/idea/
- IDEA Part B annual reports and state determinations: https://sites.ed.gov/idea/data/

### COPPA — Children's Online Privacy Protection Act

**Statute:** 15 U.S.C. §§ 6501-6506
**Regulations:** 16 CFR Part 312
**Enforcement:** Federal Trade Commission

Authoritative URLs:

- Statute: https://www.law.cornell.edu/uscode/text/15/chapter-91
- Regulations: https://www.ecfr.gov/current/title-16/chapter-I/subchapter-C/part-312
- FTC COPPA guide: https://www.ftc.gov/business-guidance/resources/complying-coppa-frequently-asked-questions
- FTC COPPA rule revision (2025 update): https://www.ftc.gov/legal-library/browse/rules/coppa-rule

### ESSA — Every Student Succeeds Act

**Statute:** Public Law 114-95
**Enforcement:** U.S. Department of Education

ESSA's relevance for K-12 EdTech is the **Tier 4 evidence** standard. ESSA defines four tiers of research evidence (Strong, Moderate, Promising, and "Demonstrates a Rationale"). Tier 4 is the entry bar for federally-funded EdTech procurement claims. Understand what that means before making evidence claims.

- ESSA evidence guidance: https://oese.ed.gov/files/2016/09/essa-evidence-policy-non-regulatory-guidance.pdf

### HIPAA — applies in narrow cases only

HIPAA generally does **not** apply to school records — those are FERPA-covered. HIPAA applies to "covered entities" (healthcare providers, health plans, healthcare clearinghouses). A school is normally not a covered entity.

The exception: school-based health services billed to Medicaid create HIPAA exposure for that specific data flow. If a future product feature touches this (e.g., integration with school nursing systems), consult counsel.

- HHS HIPAA-FERPA joint guidance: https://studentprivacy.ed.gov/joint-guidance-application-ferpa-and-hipaa-student-health-records

## State frameworks

State-level student privacy laws layer on top of federal frameworks. The patchwork is significant. The most relevant categories:

### SOPPA-IL — Student Online Personal Protection Act (Illinois)

**Statute:** 105 ILCS 85
**Effective:** July 1, 2021 amendments
**Enforcement:** Illinois Attorney General

SOPPA imposes stricter requirements on vendors of online services targeted to K-12 students in Illinois. Includes mandatory data deletion timelines, breach notification, and prohibited uses.

- Statute: https://www.ilga.gov/legislation/ilcs/ilcs5.asp?ActID=4032&ChapterID=17
- IL SBE guidance: https://www.isbe.net/Pages/SOPPA.aspx

### Similar state laws (non-exhaustive, the major ones)

| State | Law | Notable provisions |
|---|---|---|
| California | SOPIPA (Student Online Personal Info Protection Act) | Targeted advertising ban; data security requirements |
| Colorado | Privacy Act + Student Data Transparency and Security Act | Annual public report of student data agreements |
| Connecticut | CT Student Data Privacy Act | Vendor contracts and data deletion |
| New York | Education Law § 2-d | Parents' Bill of Rights; mandatory data security plan |
| Maryland | Maryland Student Data Privacy Act | Stricter than COPPA on EdTech vendors |
| Massachusetts | Student Records Regulations 603 CMR 23 | Pre-FERPA state law still in force |
| Texas | TEC Chapter 32 + 19 TAC Chapter 25 | Confidentiality of certain personally identifiable info |
| Utah | Student Data Protection Act | Notice and consent requirements |
| Virginia | Code § 22.1-289 | Annual data inventory; vendor contracts |
| Washington | RCW 28A.604 | Student data privacy and vendor obligations |

When entering a new state, **do a state-specific compliance review.** The state-by-state variation is the single largest hidden cost in K-12 EdTech expansion.

State-by-state tracker (community-maintained, useful but not authoritative):
- Foundation for Individual Rights and Expression student data tracker: https://www.thefire.org/research-learn/student-data-privacy
- A4L Student Data Privacy Consortium: https://privacy.a4l.org/

## Voluntary frameworks and pledges

### Student Privacy Pledge

A voluntary commitment from EdTech vendors.

- Pledge text: https://studentprivacypledge.org/
- Signatories list: https://studentprivacypledge.org/signatories/

### 1EdTech TrustEd Apps

EdTech app rubric. Ednition cites alignment. Useful for procurement.

- TrustEd Apps program: https://www.1edtech.org/program/trustedapps

### SOC 2 Type II

Not a privacy framework per se, but procurement-relevant. Many districts require SOC 2 reports during vendor review.

- AICPA SOC 2 reference: https://www.aicpa-cima.com/topic/audit-assurance/audit-and-assurance-greater-than-soc

## How agents should use this directory

When implementing or reasoning about a compliance requirement:

1. **Find the relevant framework** (FERPA for student records privacy, IDEA for special-ed procedures, COPPA for under-13 data collection, etc.)
2. **Read the per-framework reference doc** in this directory (`ferpa.md`, `idea.md`, etc.)
3. **Cite the authoritative source** in code comments and ADRs (statute and regulation URLs above)
4. **Re-verify shelf life** if the source is more than 12 months old or if there's been recent regulatory activity

Per the `compliance` rule: compliance math (deadline calculations, retention periods, eligibility thresholds) lives in dedicated domain services, not in controllers or models. Each compliance service has the relevant grounding source cited inline.

## Shelf life check

Re-check this directory and the URLs above:

- **Annually:** Federal frameworks (FERPA, IDEA, COPPA) rarely change but interpretive guidance is updated more often
- **Quarterly:** State frameworks (legislative session activity)
- **Before any new state expansion:** Always do a fresh review


## Verification log

When you confirm a regulatory source, note the date and any version pinning in code comments:

```python
# Per IDEA 34 CFR § 300.301(c)(1), evaluation must be completed within 60 days
# of receiving parental consent for evaluation, unless the state has established
# a different timeframe.  Verified 2026-05-11 against
# https://www.ecfr.gov/current/title-34/subtitle-B/chapter-III/part-300/subpart-D/subject-group-ECFR2c0ad2bb56b5fd5/section-300.301
TIMELINE_INITIAL_EVALUATION_DAYS = 60
```

Inline citation + verification date + URL is the discipline that keeps compliance code honest over time.
