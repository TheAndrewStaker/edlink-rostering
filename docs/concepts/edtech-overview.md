# EdTech systems overview

Read this if you're new to K-12 EdTech and unfamiliar with how district systems fit together.

## The district as the unit of customer

Every K-12 customer is either a **school district** or a **charter school**. Districts contain schools, which contain classes, which contain students. Authorization, data scope, billing, and IT contact are all per-district.

A typical district stack looks like this:

```
                    District
                       │
        ┌──────────────┼──────────────┐
        │              │              │
       SIS         IEP system        LMS
   (PowerSchool)   (Frontline)     (Canvas)
        │              │              │
   ┌────┴────┐    ┌────┴────┐    ┌────┴────┐
   Enrollment  Special-ed    Courses
   Schedules   documents    Assignments
   Attendance  Goals        Grades
   Demographics  Progress    Submissions
```

Each system has its own auth, its own data model, its own API quirks, and its own SLA. The integration framework's job is to make all of this look uniform to the application's product code and AI layer.

## SIS — Student Information System

**What it holds:** Every student in the district. Their demographics, schedule, class enrollments, attendance, grades. Teachers and their class assignments. The school calendar and bell schedule.

**Why it matters first:** Every other integration depends on the SIS being right. If your student roster is wrong, every IEP, every assignment, every progress entry is attached to the wrong person. SIS data is the foundation.

**Market share:**
- PowerSchool — about 45% of US districts. Has its own API plus OneRoster support.
- Infinite Campus — about 20%. Campus Learning API.
- Skyward — about 12%. API quality varies; some districts on older versions only do SFTP.
- Tyler/Synergy — about 8%. Native API.
- Aeries — California-heavy. Has API and OneRoster.
- Veracross — private-school-heavy.

**Access paths:**
- Direct API to the SIS (each vendor different)
- Via aggregator: Clever, ClassLink (both wrap many SIS behind a unified API)
- Via standard: OneRoster (REST or CSV) where supported
- Via aggregator that wraps the standard: EdLink, Ednition (RosterStream)

**Analog:** SIS is to schools what HRIS is to companies. Source of truth for "who is in the org and what's their context."

## IEP system

**What it holds:** The legal special-education document for each qualifying student. Each IEP contains:

- **Present levels** — academic and functional baseline measurements
- **Annual goals** — measurable goals the student is working toward, often dozens per IEP
- **Services** — specific services the school will provide (speech therapy, OT, specialized reading instruction)
- **Accommodations** — classroom adjustments (extended time on tests, preferential seating)
- **Progress notes** — periodic measurements against the goals
- **Service minutes** — required logged time delivering each service
- **Review cycle** — annual review date, triennial reevaluation

The IEP is a legal document. **Failing to deliver what's in it has legal consequences for the district.** This is why IEP data is the highest-stakes integration lane: it's the litigated domain.

**Major vendors:**
- Frontline IEP (formerly IEP Direct) — broad national footprint
- PowerSchool Special Programs (formerly TIENET) — common in PowerSchool districts
- SEIS — California state-run, used by ~95% of CA districts
- CT-SEDS — Connecticut state-run
- EdPlan, SEAS, GoalView, Embrace — smaller regional vendors

**Access paths:**
- Each vendor's proprietary API (varies wildly in quality and openness)
- State-run systems (SEIS, CT-SEDS) often require SFTP + signed state-approved-vendor agreement
- Some IEP systems live inside an SIS (PowerSchool SIS + PowerSchool Special Programs is a combined deployment)

**Critical implementation notes:**
- IEP data is always student-level. Each student has one active IEP and a history of prior IEPs.
- Goals and progress entries are time-series data. Schema needs to handle longitudinal tracking.
- Write-back is highly sensitive. Writing progress data back to the IEP system means that data becomes part of the legal record.

**Analog:** IEP system is to a district what a claims management system is to a benefits administrator. The system of record for the contracted services being delivered.

## LMS — Learning Management System

**What it holds:** Courses, assignments, submissions, grades, discussion threads, attendance against class meetings.

**Major vendors:**
- Canvas — large district / higher-ed leader
- Schoology — common in K-12
- Brightspace (D2L)
- Moodle — open-source, varied deployments
- Google Classroom — huge K-12 footprint via Google for Education
- Microsoft Teams for Education — common in Microsoft-shop districts

**Access paths:**
- **LTI 1.3** — the standard. Lets the application register as a "tool" inside any LTI-compliant LMS. Teachers launch the tool from inside their LMS, with student/course context passed in the launch. Service calls (push grades back, fetch assignments) use OAuth 2.0 with JWT client assertion. One integration covers Canvas, Schoology, Brightspace, Moodle, and many others.
- **Vendor-direct APIs** — Google Classroom and Microsoft Teams have their own REST APIs.

**Analog:** LMS is the activity layer on top of the system of record. Like time-and-attendance on top of HRIS.

## Assessment platforms

**What they hold:** Test results, growth measurements, item-level performance data.

**Major vendors:**
- NWEA MAP — gold standard for K-12 growth measurement
- iReady (Curriculum Associates)
- Renaissance Star
- DIBELS — early literacy
- State assessments — SBAC, PARCC, state-specific. These typically flow through state-level Ed-Fi feeds, not vendor APIs.

**Access paths:**
- Rostering and SSO via Clever / ClassLink / EdLink (covers most)
- Direct vendor API for actual score data (NWEA MAP API, iReady API, etc.)
- State assessments via Ed-Fi over SFTP with mTLS, or state-specific portal

**Pattern:** Two integrations per vendor — identity layer via an aggregator, performance data direct.

**Analog:** Assessment platforms hold the utilization data on top of enrollment. Like claims data showing what services were actually rendered.

## Putting it together

For a special education application, a complete workflow touches all four:

1. **SIS** tells the application which students are at which school in which classes with which teachers.
2. **IEP system** tells the application which of those students have IEPs, what goals they're working toward, and what services they're entitled to.
3. **LMS** holds the day-to-day classroom activity that progress can be measured against.
4. **Assessment platforms** hold standardized measurements that supplement teacher-collected progress data.

An AI layer can read across all four and produce summaries, recommendations, and progress narratives that teachers can use to reduce administrative load. The integration framework's job is to make all four sources look uniform to that AI layer.
