"""
LMS Routes - Education & Learning Management System

FastAPI routes for the enterprise LMS module with HR integration.
Supports courses, enrollments, certificates, quizzes, and gamification.
"""
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse, FileResponse, JSONResponse
from deps import flash, template_context, require_auth, login_required, admin_required, current_company
from template_engine import templates
import logging

logger = logging.getLogger(__name__)

from datetime import datetime, date, timedelta
from lms_data_store import lms_store

router = APIRouter(prefix="/lms", tags=["lms"])


def _company(request: Request) -> str:
    """Get current company ID from session."""
    # request.state.company_id (tenant middleware) wins; otherwise the unified
    # session resolution from deps. The legacy session["company_id"] key was
    # never written anywhere, so that dead lookup was dropped.
    return getattr(request.state, "company_id", None) or current_company(request)


def _user(request: Request) -> dict:
    """Get current user info from session."""
    return {
        'user_id': request.session.get('user_id', ''),
        'username': request.session.get('username', ''),
        'full_name': request.session.get('full_name', ''),
    }


# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/", name="lms_dashboard")
@router.get("/dashboard", name="lms_dashboard_alt")
async def dashboard(request: Request, user=Depends(login_required)):
    """LMS main dashboard with stats and quick access."""
    company_id = _company(request)
    current_user = _user(request)
    
    # Get dashboard stats
    stats = lms_store.get_dashboard_stats(company_id)
    
    # Get user's enrollments
    my_enrollments = lms_store.get_user_enrollments(
        current_user['user_id'], company_id
    )
    in_progress = [e for e in my_enrollments if e.get('status') == 'in_progress']
    
    # Get featured courses
    featured_courses = lms_store.get_courses(company_id, status='published')[:6]
    
    # Get learning paths
    learning_paths = lms_store.get_learning_paths(company_id, status='published')[:4]
    
    # Get user's gamification profile
    gamification = lms_store.get_gamification_profile(current_user['user_id'], company_id)
    
    # Get leaderboard
    leaderboard = lms_store.get_leaderboard(company_id, limit=5)
    
    # Get expiring certificates
    expiring_certs = lms_store.get_expiring_certificates(30, company_id)
    my_expiring = [c for c in expiring_certs if c.get('user_id') == current_user['user_id']]
    
    ctx = template_context(request)
    ctx.update(
        stats=stats,
        in_progress=in_progress[:5],
        featured_courses=featured_courses,
        learning_paths=learning_paths,
        gamification=gamification,
        leaderboard=leaderboard,
        expiring_certificates=my_expiring,
    )
    return templates.TemplateResponse("lms/dashboard.html", ctx)


# ══════════════════════════════════════════════════════════════════════════════
# COURSES - CATALOG & ENROLLMENT
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/courses", name="lms_course_catalog")
async def course_catalog(request: Request, user=Depends(login_required)):
    """Browse available courses."""
    company_id = _company(request)
    current_user = _user(request)
    
    # Get filters from query params
    category = request.query_params.get("category", "")
    skill_level = request.query_params.get("skill_level", "")
    compliance_only = request.query_params.get("compliance", "") == "true"
    search = request.query_params.get("search", "").strip().lower()
    
    # Get courses
    courses = lms_store.get_courses(
        company_id, 
        status='published',
        is_compliance=compliance_only if compliance_only else None,
        category=category if category else None
    )
    
    # Apply search filter
    if search:
        courses = [c for c in courses if 
                   search in c.get('title', '').lower() or
                   search in c.get('description', '').lower()]
    
    # Apply skill level filter
    if skill_level:
        courses = [c for c in courses if c.get('skill_level') == skill_level]
    
    # Get user's enrollments to show status
    enrollments = lms_store.get_user_enrollments(current_user['user_id'], company_id)
    enrolled_course_ids = {e['course_id']: e for e in enrollments if e.get('course_id')}
    
    # Get categories for filter
    categories = lms_store.get_course_categories(company_id)
    
    ctx = template_context(request)
    ctx.update(
        courses=courses,
        enrolled_course_ids=enrolled_course_ids,
        categories=categories,
        selected_category=category,
        selected_skill_level=skill_level,
        compliance_only=compliance_only,
        search_query=request.query_params.get("search", ""),
    )
    return templates.TemplateResponse("lms/course_catalog.html", ctx)


@router.get("/courses/{course_id}", name="lms_course_detail")
async def course_detail(course_id: str, request: Request, user=Depends(login_required)):
    """View course details."""
    company_id = _company(request)
    current_user = _user(request)
    
    course = lms_store.get_course(course_id, company_id)
    if not course:
        flash(request, "Course not found", "error")
        return RedirectResponse("/lms/courses", status_code=302)
    
    # Check enrollment status
    enrollments = lms_store.get_user_enrollments(current_user['user_id'], company_id)
    enrollment = next((e for e in enrollments if e.get('course_id') == course_id), None)
    
    ctx = template_context(request)
    ctx.update(
        course=course,
        enrollment=enrollment,
        is_enrolled=enrollment is not None,
    )
    return templates.TemplateResponse("lms/course_detail.html", ctx)


@router.post("/courses/{course_id}/enroll", name="lms_enroll_course")
async def enroll_course(course_id: str, request: Request, user=Depends(login_required)):
    """Enroll in a course."""
    company_id = _company(request)
    current_user = _user(request)
    
    enrollment_id = lms_store.enroll_user({
        'user_id': current_user['user_id'],
        'username': current_user['username'],
        'course_id': course_id,
        'company_id': company_id,
        'assignment_type': 'self_enrolled',
    })
    
    if enrollment_id:
        flash(request, "Successfully enrolled in course!", "success")
        # Award points for enrolling
        lms_store.award_points(current_user['user_id'], 10, "Course enrollment", company_id)
    else:
        flash(request, "Already enrolled or enrollment failed", "error")
    
    return RedirectResponse(f"/lms/courses/{course_id}", status_code=303)


@router.get("/courses/{course_id}/learn", name="lms_learn_course")
async def learn_course(course_id: str, request: Request, user=Depends(login_required)):
    """Course learning interface."""
    company_id = _company(request)
    current_user = _user(request)
    
    course = lms_store.get_course(course_id, company_id)
    if not course:
        flash(request, "Course not found", "error")
        return RedirectResponse("/lms/courses", status_code=302)
    
    # Check enrollment
    enrollments = lms_store.get_user_enrollments(current_user['user_id'], company_id)
    enrollment = next((e for e in enrollments if e.get('course_id') == course_id), None)
    
    if not enrollment:
        flash(request, "Please enroll in this course first", "warning")
        return RedirectResponse(f"/lms/courses/{course_id}", status_code=302)
    
    # Update last accessed
    lms_store.update_enrollment_progress(
        enrollment['enrollment_id'],
        enrollment.get('progress_percent', 0)
    )
    
    ctx = template_context(request)
    ctx.update(
        course=course,
        enrollment=enrollment,
    )
    return templates.TemplateResponse("lms/course_learn.html", ctx)


@router.post("/courses/{course_id}/progress", name="lms_update_progress")
async def update_progress(course_id: str, request: Request, user=Depends(login_required)):
    """Update course progress (API endpoint)."""
    company_id = _company(request)
    current_user = _user(request)
    
    data = await request.json()
    progress = float(data.get('progress', 0))
    time_spent = int(data.get('time_spent', 0))
    
    # Get enrollment
    enrollments = lms_store.get_user_enrollments(current_user['user_id'], company_id)
    enrollment = next((e for e in enrollments if e.get('course_id') == course_id), None)
    
    if not enrollment:
        return JSONResponse({'success': False, 'error': 'Not enrolled'}, status_code=400)
    
    lms_store.update_enrollment_progress(
        enrollment['enrollment_id'],
        progress,
        time_spent
    )
    
    return JSONResponse({'success': True, 'progress': progress})


@router.post("/courses/{course_id}/complete", name="lms_complete_course")
async def complete_course(course_id: str, request: Request, user=Depends(login_required)):
    """Mark course as complete."""
    company_id = _company(request)
    current_user = _user(request)
    
    data = await request.form()
    score = float(data.get('score', 100))
    
    # Get enrollment
    enrollments = lms_store.get_user_enrollments(current_user['user_id'], company_id)
    enrollment = next((e for e in enrollments if e.get('course_id') == course_id), None)
    
    if not enrollment:
        flash(request, "Not enrolled in this course", "error")
        return RedirectResponse(f"/lms/courses/{course_id}", status_code=302)
    
    cert_id = lms_store.complete_enrollment(enrollment['enrollment_id'], score)
    
    if cert_id:
        flash(request, "Congratulations! Course completed and certificate issued!", "success")
        # Award points and check for badges
        lms_store.award_points(current_user['user_id'], 100, "Course completion", company_id)
        
        # Check for first course badge
        profile = lms_store.get_gamification_profile(current_user['user_id'], company_id)
        if profile.get('courses_completed', 0) == 0:
            lms_store.award_badge(current_user['user_id'], 'first_course', company_id)
        
        if score == 100:
            lms_store.award_badge(current_user['user_id'], 'perfect_score', company_id)
        
        return RedirectResponse(f"/lms/certificates/{cert_id}", status_code=303)
    else:
        flash(request, "Course marked as completed", "success")
        return RedirectResponse("/lms/my-learning", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# MY LEARNING
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/my-learning", name="lms_my_learning")
async def my_learning(request: Request, user=Depends(login_required)):
    """View user's enrolled courses and progress."""
    company_id = _company(request)
    current_user = _user(request)
    
    # Get all enrollments
    enrollments = lms_store.get_user_enrollments(current_user['user_id'], company_id)
    
    # Categorize
    in_progress = [e for e in enrollments if e.get('status') == 'in_progress']
    completed = [e for e in enrollments if e.get('status') == 'completed']
    not_started = [e for e in enrollments if e.get('status') == 'enrolled']
    
    # Get certificates
    certificates = lms_store.get_user_certificates(current_user['user_id'], company_id)
    
    # Get gamification profile
    gamification = lms_store.get_gamification_profile(current_user['user_id'], company_id)
    
    ctx = template_context(request)
    ctx.update(
        in_progress=in_progress,
        completed=completed,
        not_started=not_started,
        total_enrolled=len(enrollments),
        certificates=certificates,
        gamification=gamification,
    )
    return templates.TemplateResponse("lms/my_learning.html", ctx)


# ══════════════════════════════════════════════════════════════════════════════
# CERTIFICATES
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/certificates", name="lms_certificates_list")
async def certificates_list(request: Request, user=Depends(login_required)):
    """View user's certificates."""
    company_id = _company(request)
    current_user = _user(request)
    
    certificates = lms_store.get_user_certificates(current_user['user_id'], company_id)
    
    # Categorize by status
    valid = [c for c in certificates if c.get('status') == 'valid']
    expired = [c for c in certificates if c.get('status') == 'expired' or 
               (c.get('expiry_date') and c['expiry_date'] < date.today())]
    
    # Get expiring soon (within 30 days)
    expiring_soon = [c for c in valid if c.get('expiry_date') and 
                     c['expiry_date'] <= date.today() + timedelta(days=30)]
    
    ctx = template_context(request)
    ctx.update(
        certificates=certificates,
        valid_count=len(valid),
        expired_count=len(expired),
        expiring_soon=expiring_soon,
    )
    return templates.TemplateResponse("lms/certificates.html", ctx)


@router.get("/certificates/{certificate_id}", name="lms_certificate_detail")
async def certificate_detail(certificate_id: str, request: Request, user=Depends(login_required)):
    """View a specific certificate."""
    certificate = lms_store.get_certificate(certificate_id)
    
    if not certificate:
        flash(request, "Certificate not found", "error")
        return RedirectResponse("/lms/certificates", status_code=302)
    
    ctx = template_context(request)
    ctx.update(certificate=certificate)
    return templates.TemplateResponse("lms/certificate_detail.html", ctx)


@router.get("/certificates/{certificate_id}/download", name="lms_certificate_download")
async def certificate_download(certificate_id: str, request: Request, user=Depends(login_required)):
    """Download certificate as PDF."""
    certificate = lms_store.get_certificate(certificate_id)
    
    if not certificate:
        flash(request, "Certificate not found", "error")
        return RedirectResponse("/lms/certificates", status_code=302)
    
    # TODO: Generate PDF certificate
    # For now, redirect to detail page
    flash(request, "PDF download coming soon", "info")
    return RedirectResponse(f"/lms/certificates/{certificate_id}", status_code=302)


# ══════════════════════════════════════════════════════════════════════════════
# QUIZZES
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/quiz/{quiz_id}", name="lms_take_quiz")
async def take_quiz(quiz_id: str, request: Request, user=Depends(login_required)):
    """Take a quiz."""
    current_user = _user(request)
    
    quiz = lms_store.get_quiz(quiz_id)
    if not quiz:
        flash(request, "Quiz not found", "error")
        return RedirectResponse("/lms/my-learning", status_code=302)
    
    ctx = template_context(request)
    ctx.update(quiz=quiz)
    return templates.TemplateResponse("lms/quiz.html", ctx)


@router.post("/quiz/{quiz_id}/submit", name="lms_submit_quiz")
async def submit_quiz(quiz_id: str, request: Request, user=Depends(login_required)):
    """Submit quiz answers."""
    company_id = _company(request)
    current_user = _user(request)
    
    form = await request.form()
    answers = {}
    for key, value in form.items():
        if key.startswith('q_'):
            q_id = key[2:]
            answers[q_id] = value
    
    result = lms_store.submit_quiz_attempt({
        'quiz_id': quiz_id,
        'user_id': current_user['user_id'],
        'company_id': company_id,
        'answers': answers,
    })
    
    if result.get('success'):
        if result.get('passed'):
            flash(request, f"Congratulations! You passed with {result['score']:.1f}%", "success")
            lms_store.award_points(current_user['user_id'], 50, "Quiz passed", company_id)
        else:
            flash(request, f"You scored {result['score']:.1f}%. You need {70}% to pass.", "warning")
    else:
        flash(request, f"Error: {result.get('error')}", "error")
    
    return RedirectResponse("/lms/my-learning", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# LEARNING PATHS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/paths", name="lms_learning_paths")
async def learning_paths(request: Request, user=Depends(login_required)):
    """Browse learning paths."""
    company_id = _company(request)
    
    paths = lms_store.get_learning_paths(company_id, status='published')
    
    # Enrich with course details
    for path in paths:
        course_ids = path.get('course_ids', [])
        path['courses'] = []
        for cid in course_ids:
            course = lms_store.get_course(cid, company_id)
            if course:
                path['courses'].append(course)
        path['total_duration'] = sum(c.get('duration_minutes', 0) for c in path['courses'])
    
    ctx = template_context(request)
    ctx.update(paths=paths)
    return templates.TemplateResponse("lms/learning_paths.html", ctx)


@router.get("/paths/{path_id}", name="lms_path_detail")
async def path_detail(path_id: str, request: Request, user=Depends(login_required)):
    """View learning path details."""
    company_id = _company(request)
    
    path = lms_store.get_learning_path(path_id, company_id)
    if not path:
        flash(request, "Learning path not found", "error")
        return RedirectResponse("/lms/paths", status_code=302)
    
    # Get courses in path
    courses = []
    for cid in path.get('course_ids', []):
        course = lms_store.get_course(cid, company_id)
        if course:
            courses.append(course)
    
    ctx = template_context(request)
    ctx.update(
        path=path,
        courses=courses,
        total_duration=sum(c.get('duration_minutes', 0) for c in courses),
    )
    return templates.TemplateResponse("lms/path_detail.html", ctx)


# ══════════════════════════════════════════════════════════════════════════════
# LEADERBOARD & GAMIFICATION
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/leaderboard", name="lms_leaderboard")
async def leaderboard(request: Request, user=Depends(login_required)):
    """View gamification leaderboard."""
    company_id = _company(request)
    current_user = _user(request)
    
    leaders = lms_store.get_leaderboard(company_id, limit=50)
    my_profile = lms_store.get_gamification_profile(current_user['user_id'], company_id)
    
    ctx = template_context(request)
    ctx.update(
        leaders=leaders,
        my_profile=my_profile,
    )
    return templates.TemplateResponse("lms/leaderboard.html", ctx)


@router.get("/badges", name="lms_badges")
async def badges(request: Request, user=Depends(login_required)):
    """View available badges."""
    company_id = _company(request)
    current_user = _user(request)
    
    from models.lms import BADGES
    
    my_profile = lms_store.get_gamification_profile(current_user['user_id'], company_id)
    earned_badge_ids = [b.get('badge_id') for b in my_profile.get('badges', [])]
    
    all_badges = []
    for badge_id, badge_info in BADGES.items():
        all_badges.append({
            'badge_id': badge_id,
            **badge_info,
            'earned': badge_id in earned_badge_ids,
        })
    
    ctx = template_context(request)
    ctx.update(
        badges=all_badges,
        earned_count=len(earned_badge_ids),
        total_count=len(BADGES),
    )
    return templates.TemplateResponse("lms/badges.html", ctx)


# ══════════════════════════════════════════════════════════════════════════════
# RESOURCE LIBRARY
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/resources", name="lms_resource_library")
async def resource_library(request: Request, user=Depends(login_required)):
    """Browse resource library."""
    company_id = _company(request)
    
    # TODO: Implement resource retrieval
    resources = []
    
    ctx = template_context(request)
    ctx.update(resources=resources)
    return templates.TemplateResponse("lms/resources.html", ctx)


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN - COURSE MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/admin", name="lms_admin_dashboard")
async def admin_dashboard(request: Request, user=Depends(admin_required)):
    """LMS admin dashboard."""
    company_id = _company(request)
    
    stats = lms_store.get_dashboard_stats(company_id)
    courses = lms_store.get_courses(company_id)
    paths = lms_store.get_learning_paths(company_id)
    
    ctx = template_context(request)
    ctx.update(
        stats=stats,
        courses=courses,
        paths=paths,
    )
    return templates.TemplateResponse("lms/admin/dashboard.html", ctx)


@router.get("/admin/courses", name="lms_admin_courses")
async def admin_courses(request: Request, user=Depends(admin_required)):
    """Manage courses."""
    company_id = _company(request)
    
    courses = lms_store.get_courses(company_id)
    categories = lms_store.get_course_categories(company_id)
    
    ctx = template_context(request)
    ctx.update(
        courses=courses,
        categories=categories,
    )
    return templates.TemplateResponse("lms/admin/courses.html", ctx)


@router.get("/admin/courses/add", name="lms_admin_add_course_get")
async def admin_add_course_get(request: Request, user=Depends(admin_required)):
    """Add new course form."""
    company_id = _company(request)
    categories = lms_store.get_course_categories(company_id)
    
    ctx = template_context(request)
    ctx.update(
        course={},
        categories=categories,
        content_types=['text', 'video', 'pdf', 'scorm_1.2', 'scorm_2004', 'xapi', 'html5', 'quiz'],
    )
    return templates.TemplateResponse("lms/admin/course_form.html", ctx)


@router.post("/admin/courses/add", name="lms_admin_add_course")
async def admin_add_course(request: Request, user=Depends(admin_required)):
    """Create a new course."""
    company_id = _company(request)
    current_user = _user(request)
    
    form = await request.form()
    
    course_data = {
        'company_id': company_id,
        'title': form.get('title', '').strip(),
        'description': form.get('description', ''),
        'short_description': form.get('short_description', ''),
        'content_type': form.get('content_type', 'text'),
        'content_url': form.get('content_url', ''),
        'duration_minutes': int(form.get('duration_minutes', 0) or 0),
        'category': form.get('category', ''),
        'skill_level': form.get('skill_level', 'beginner'),
        'passing_score': int(form.get('passing_score', 70) or 70),
        'is_compliance_required': form.get('is_compliance_required') == 'on',
        'compliance_category': form.get('compliance_category', ''),
        'validity_period_days': int(form.get('validity_period_days', 365) or 365),
        'status': form.get('status', 'draft'),
        'created_by': current_user['username'],
    }
    
    # Parse tags
    tags_str = form.get('tags', '')
    course_data['tags'] = [t.strip() for t in tags_str.split(',') if t.strip()]
    
    course_id = lms_store.create_course(course_data)
    
    if course_id:
        flash(request, f"Course '{course_data['title']}' created successfully!", "success")
        return RedirectResponse("/lms/admin/courses", status_code=303)
    else:
        flash(request, "Failed to create course", "error")
        return RedirectResponse("/lms/admin/courses/add", status_code=303)


@router.get("/admin/courses/{course_id}/edit", name="lms_admin_edit_course_get")
async def admin_edit_course_get(course_id: str, request: Request, user=Depends(admin_required)):
    """Edit course form."""
    company_id = _company(request)
    
    course = lms_store.get_course(course_id, company_id)
    if not course:
        flash(request, "Course not found", "error")
        return RedirectResponse("/lms/admin/courses", status_code=302)
    
    categories = lms_store.get_course_categories(company_id)
    
    ctx = template_context(request)
    ctx.update(
        course=course,
        categories=categories,
        content_types=['text', 'video', 'pdf', 'scorm_1.2', 'scorm_2004', 'xapi', 'html5', 'quiz'],
        is_edit=True,
    )
    return templates.TemplateResponse("lms/admin/course_form.html", ctx)


@router.post("/admin/courses/{course_id}/edit", name="lms_admin_edit_course")
async def admin_edit_course(course_id: str, request: Request, user=Depends(admin_required)):
    """Update a course."""
    form = await request.form()
    
    course_data = {
        'title': form.get('title', '').strip(),
        'description': form.get('description', ''),
        'short_description': form.get('short_description', ''),
        'content_type': form.get('content_type', 'text'),
        'content_url': form.get('content_url', ''),
        'duration_minutes': int(form.get('duration_minutes', 0) or 0),
        'category': form.get('category', ''),
        'skill_level': form.get('skill_level', 'beginner'),
        'passing_score': int(form.get('passing_score', 70) or 70),
        'is_compliance_required': form.get('is_compliance_required') == 'on',
        'compliance_category': form.get('compliance_category', ''),
        'validity_period_days': int(form.get('validity_period_days', 365) or 365),
        'status': form.get('status', 'draft'),
    }
    
    # Parse tags
    tags_str = form.get('tags', '')
    course_data['tags'] = [t.strip() for t in tags_str.split(',') if t.strip()]
    
    if lms_store.update_course(course_id, course_data):
        flash(request, "Course updated successfully!", "success")
    else:
        flash(request, "Failed to update course", "error")
    
    return RedirectResponse("/lms/admin/courses", status_code=303)


@router.post("/admin/courses/{course_id}/delete", name="lms_admin_delete_course")
async def admin_delete_course(course_id: str, request: Request, user=Depends(admin_required)):
    """Delete a course."""
    if lms_store.delete_course(course_id):
        flash(request, "Course deleted", "success")
    else:
        flash(request, "Failed to delete course", "error")
    
    return RedirectResponse("/lms/admin/courses", status_code=303)


@router.post("/admin/courses/{course_id}/publish", name="lms_admin_publish_course")
async def admin_publish_course(course_id: str, request: Request, user=Depends(admin_required)):
    """Publish a course."""
    if lms_store.update_course(course_id, {'status': 'published'}):
        flash(request, "Course published!", "success")
    else:
        flash(request, "Failed to publish course", "error")
    
    return RedirectResponse("/lms/admin/courses", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN - ENROLLMENTS & REPORTS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/admin/enrollments", name="lms_admin_enrollments")
async def admin_enrollments(request: Request, user=Depends(admin_required)):
    """View all enrollments."""
    company_id = _company(request)
    
    # TODO: Get all enrollments with pagination
    ctx = template_context(request)
    ctx.update(enrollments=[])
    return templates.TemplateResponse("lms/admin/enrollments.html", ctx)


@router.get("/admin/reports", name="lms_admin_reports")
async def admin_reports(request: Request, user=Depends(admin_required)):
    """LMS reports and analytics."""
    company_id = _company(request)
    
    stats = lms_store.get_dashboard_stats(company_id)
    expiring_certs = lms_store.get_expiring_certificates(30, company_id)
    
    ctx = template_context(request)
    ctx.update(
        stats=stats,
        expiring_certificates=expiring_certs,
    )
    return templates.TemplateResponse("lms/admin/reports.html", ctx)


@router.get("/admin/compliance", name="lms_admin_compliance")
async def admin_compliance(request: Request, user=Depends(admin_required)):
    """Compliance training status."""
    company_id = _company(request)
    
    # Get compliance courses
    compliance_courses = lms_store.get_courses(company_id, status='published', is_compliance=True)
    
    # Get expiring certificates
    expiring_certs = lms_store.get_expiring_certificates(60, company_id)
    
    ctx = template_context(request)
    ctx.update(
        compliance_courses=compliance_courses,
        expiring_certificates=expiring_certs,
    )
    return templates.TemplateResponse("lms/admin/compliance.html", ctx)


# ══════════════════════════════════════════════════════════════════════════════
# MANAGER - TEAM REPORTS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/manager/team", name="lms_manager_team")
async def manager_team(request: Request, user=Depends(login_required)):
    """Manager view of team learning progress."""
    company_id = _company(request)
    current_user = _user(request)
    
    # Check if user is a manager (has direct reports)
    # This would typically integrate with HR module
    
    team_report = lms_store.get_manager_team_report(current_user['user_id'], company_id)
    
    ctx = template_context(request)
    ctx.update(team_report=team_report)
    return templates.TemplateResponse("lms/manager/team.html", ctx)


@router.get("/manager/skill-matrix", name="lms_manager_skill_matrix")
async def manager_skill_matrix(request: Request, user=Depends(login_required)):
    """View team skill matrix."""
    company_id = _company(request)
    current_user = _user(request)
    
    # Get skill matrix for current user (or their team if manager)
    skill_matrix = lms_store.get_skill_matrix(current_user['user_id'], company_id)
    
    # Get recommended courses
    recommended = lms_store.recommend_courses_for_skill_gaps(current_user['user_id'], company_id)
    
    ctx = template_context(request)
    ctx.update(
        skill_matrix=skill_matrix,
        recommended_courses=recommended,
    )
    return templates.TemplateResponse("lms/manager/skill_matrix.html", ctx)
