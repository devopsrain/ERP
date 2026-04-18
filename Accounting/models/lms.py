"""
Education & Learning Management System (LMS) Data Models

Enterprise-grade LMS module integrated with HR for automated training workflows.
Supports SCORM, xAPI, video content, and certification tracking.
"""
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any
from enum import Enum
from decimal import Decimal
import uuid


class ContentType(str, Enum):
    """Supported learning content formats."""
    SCORM_12 = "scorm_1.2"
    SCORM_2004 = "scorm_2004"
    XAPI = "xapi"
    VIDEO = "video"
    PDF = "pdf"
    HTML5 = "html5"
    QUIZ = "quiz"
    SURVEY = "survey"
    TEXT = "text"
    INTERACTIVE = "interactive"


class CourseStatus(str, Enum):
    """Course publication status."""
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"
    UNDER_REVIEW = "under_review"


class EnrollmentStatus(str, Enum):
    """Learner enrollment status."""
    ENROLLED = "enrolled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"
    WITHDRAWN = "withdrawn"


class CertificateStatus(str, Enum):
    """Certificate validity status."""
    VALID = "valid"
    EXPIRED = "expired"
    REVOKED = "revoked"
    PENDING = "pending"


class LearnerRole(str, Enum):
    """LMS role-based access control."""
    LEARNER = "learner"
    INSTRUCTOR = "instructor"
    COURSE_ADMIN = "course_admin"
    AUDITOR = "auditor"
    LMS_ADMIN = "lms_admin"


class AssignmentType(str, Enum):
    """How training was assigned."""
    MANUAL = "manual"
    AUTO_ONBOARDING = "auto_onboarding"
    PERFORMANCE_REVIEW = "performance_review"
    COMPLIANCE_REQUIRED = "compliance_required"
    CAREER_PATH = "career_path"
    SELF_ENROLLED = "self_enrolled"


@dataclass
class Course:
    """
    Training course with multi-format content support.
    """
    course_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    company_id: str = "default"
    
    # Basic info
    title: str = ""
    description: str = ""
    short_description: str = ""
    thumbnail_url: str = ""
    
    # Content
    content_type: ContentType = ContentType.TEXT
    content_url: str = ""  # URL to SCORM package, video, etc.
    duration_minutes: int = 0
    
    # Categorization
    category: str = ""
    tags: List[str] = field(default_factory=list)
    skill_level: str = "beginner"  # beginner, intermediate, advanced
    language: str = "en"
    
    # Requirements
    prerequisites: List[str] = field(default_factory=list)  # course_ids
    passing_score: int = 70  # percentage
    max_attempts: int = 3
    
    # Compliance
    is_compliance_required: bool = False
    compliance_category: str = ""  # safety, ethics, legal, etc.
    validity_period_days: int = 365  # certificate expiration
    
    # Metadata
    status: CourseStatus = CourseStatus.DRAFT
    created_by: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    published_at: Optional[datetime] = None
    
    # Stats
    total_enrollments: int = 0
    completion_rate: float = 0.0
    average_score: float = 0.0
    average_time_minutes: float = 0.0
    
    def to_dict(self) -> dict:
        return {
            'course_id': self.course_id,
            'company_id': self.company_id,
            'title': self.title,
            'description': self.description,
            'short_description': self.short_description,
            'thumbnail_url': self.thumbnail_url,
            'content_type': self.content_type.value if isinstance(self.content_type, ContentType) else self.content_type,
            'content_url': self.content_url,
            'duration_minutes': self.duration_minutes,
            'category': self.category,
            'tags': self.tags,
            'skill_level': self.skill_level,
            'language': self.language,
            'prerequisites': self.prerequisites,
            'passing_score': self.passing_score,
            'max_attempts': self.max_attempts,
            'is_compliance_required': self.is_compliance_required,
            'compliance_category': self.compliance_category,
            'validity_period_days': self.validity_period_days,
            'status': self.status.value if isinstance(self.status, CourseStatus) else self.status,
            'created_by': self.created_by,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'published_at': self.published_at.isoformat() if self.published_at else None,
            'total_enrollments': self.total_enrollments,
            'completion_rate': self.completion_rate,
            'average_score': self.average_score,
            'average_time_minutes': self.average_time_minutes,
        }


@dataclass
class LearningPath:
    """
    Structured curriculum grouping multiple courses.
    Used for certification tracks and role-based training.
    """
    path_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    company_id: str = "default"
    
    title: str = ""
    description: str = ""
    thumbnail_url: str = ""
    
    # Courses in order
    course_ids: List[str] = field(default_factory=list)
    
    # Target audience
    target_roles: List[str] = field(default_factory=list)  # job titles
    target_departments: List[str] = field(default_factory=list)
    
    # Certification
    grants_certification: bool = False
    certification_name: str = ""
    certification_validity_days: int = 365
    
    # Metadata
    status: CourseStatus = CourseStatus.DRAFT
    created_by: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)
    
    # Stats
    total_enrollments: int = 0
    completion_rate: float = 0.0
    average_completion_days: float = 0.0
    
    def to_dict(self) -> dict:
        return {
            'path_id': self.path_id,
            'company_id': self.company_id,
            'title': self.title,
            'description': self.description,
            'thumbnail_url': self.thumbnail_url,
            'course_ids': self.course_ids,
            'target_roles': self.target_roles,
            'target_departments': self.target_departments,
            'grants_certification': self.grants_certification,
            'certification_name': self.certification_name,
            'certification_validity_days': self.certification_validity_days,
            'status': self.status.value if isinstance(self.status, CourseStatus) else self.status,
            'created_by': self.created_by,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'total_enrollments': self.total_enrollments,
            'completion_rate': self.completion_rate,
            'average_completion_days': self.average_completion_days,
        }


@dataclass
class Enrollment:
    """
    Learner enrollment in a course or learning path.
    """
    enrollment_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    company_id: str = "default"
    
    # Learner
    user_id: str = ""
    username: str = ""
    
    # What they're enrolled in
    course_id: Optional[str] = None
    learning_path_id: Optional[str] = None
    
    # Assignment
    assignment_type: AssignmentType = AssignmentType.SELF_ENROLLED
    assigned_by: str = ""
    assigned_at: datetime = field(default_factory=datetime.utcnow)
    due_date: Optional[date] = None
    
    # Progress
    status: EnrollmentStatus = EnrollmentStatus.ENROLLED
    progress_percent: float = 0.0
    score: Optional[float] = None
    attempts: int = 0
    
    # Time tracking
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    last_accessed_at: Optional[datetime] = None
    time_spent_minutes: int = 0
    
    # For learning paths: track individual course progress
    course_progress: Dict[str, float] = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return {
            'enrollment_id': self.enrollment_id,
            'company_id': self.company_id,
            'user_id': self.user_id,
            'username': self.username,
            'course_id': self.course_id,
            'learning_path_id': self.learning_path_id,
            'assignment_type': self.assignment_type.value if isinstance(self.assignment_type, AssignmentType) else self.assignment_type,
            'assigned_by': self.assigned_by,
            'assigned_at': self.assigned_at.isoformat() if self.assigned_at else None,
            'due_date': self.due_date.isoformat() if self.due_date else None,
            'status': self.status.value if isinstance(self.status, EnrollmentStatus) else self.status,
            'progress_percent': self.progress_percent,
            'score': self.score,
            'attempts': self.attempts,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'last_accessed_at': self.last_accessed_at.isoformat() if self.last_accessed_at else None,
            'time_spent_minutes': self.time_spent_minutes,
            'course_progress': self.course_progress,
        }


@dataclass
class Certificate:
    """
    Training completion certificate with expiration tracking.
    """
    certificate_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    company_id: str = "default"
    
    # Recipient
    user_id: str = ""
    username: str = ""
    full_name: str = ""
    
    # What was completed
    course_id: Optional[str] = None
    learning_path_id: Optional[str] = None
    course_title: str = ""
    
    # Certificate details
    certificate_number: str = ""
    certificate_name: str = ""
    issue_date: date = field(default_factory=date.today)
    expiry_date: Optional[date] = None
    
    # Score and status
    score: float = 0.0
    status: CertificateStatus = CertificateStatus.VALID
    
    # PDF storage
    pdf_url: str = ""
    
    # Compliance tracking
    is_compliance_certificate: bool = False
    compliance_category: str = ""
    attached_to_personnel_file: bool = False
    
    # Metadata
    issued_by: str = ""
    revoked_by: str = ""
    revoked_at: Optional[datetime] = None
    revocation_reason: str = ""
    
    def to_dict(self) -> dict:
        return {
            'certificate_id': self.certificate_id,
            'company_id': self.company_id,
            'user_id': self.user_id,
            'username': self.username,
            'full_name': self.full_name,
            'course_id': self.course_id,
            'learning_path_id': self.learning_path_id,
            'course_title': self.course_title,
            'certificate_number': self.certificate_number,
            'certificate_name': self.certificate_name,
            'issue_date': self.issue_date.isoformat() if self.issue_date else None,
            'expiry_date': self.expiry_date.isoformat() if self.expiry_date else None,
            'score': self.score,
            'status': self.status.value if isinstance(self.status, CertificateStatus) else self.status,
            'pdf_url': self.pdf_url,
            'is_compliance_certificate': self.is_compliance_certificate,
            'compliance_category': self.compliance_category,
            'attached_to_personnel_file': self.attached_to_personnel_file,
            'issued_by': self.issued_by,
            'revoked_by': self.revoked_by,
            'revoked_at': self.revoked_at.isoformat() if self.revoked_at else None,
            'revocation_reason': self.revocation_reason,
        }
    
    @property
    def is_expired(self) -> bool:
        if not self.expiry_date:
            return False
        return date.today() > self.expiry_date
    
    @property
    def days_until_expiry(self) -> Optional[int]:
        if not self.expiry_date:
            return None
        delta = self.expiry_date - date.today()
        return delta.days


@dataclass
class Quiz:
    """
    Assessment quiz with multiple question types.
    """
    quiz_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    course_id: str = ""
    company_id: str = "default"
    
    title: str = ""
    description: str = ""
    
    # Settings
    passing_score: int = 70
    time_limit_minutes: int = 0  # 0 = no limit
    randomize_questions: bool = False
    show_correct_answers: bool = True
    max_attempts: int = 3
    
    # Questions (stored as JSON)
    questions: List[Dict[str, Any]] = field(default_factory=list)
    # Question format:
    # {
    #   "id": "q1",
    #   "type": "multiple_choice" | "true_false" | "fill_blank" | "matching",
    #   "text": "Question text",
    #   "options": ["A", "B", "C", "D"],
    #   "correct_answer": "A" | ["A", "C"] for multi-select,
    #   "points": 10,
    #   "explanation": "Why this is correct"
    # }
    
    total_points: int = 0
    created_by: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)
    
    def to_dict(self) -> dict:
        return {
            'quiz_id': self.quiz_id,
            'course_id': self.course_id,
            'company_id': self.company_id,
            'title': self.title,
            'description': self.description,
            'passing_score': self.passing_score,
            'time_limit_minutes': self.time_limit_minutes,
            'randomize_questions': self.randomize_questions,
            'show_correct_answers': self.show_correct_answers,
            'max_attempts': self.max_attempts,
            'questions': self.questions,
            'total_points': self.total_points,
            'created_by': self.created_by,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


@dataclass
class QuizAttempt:
    """
    Record of a learner's quiz attempt.
    """
    attempt_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    quiz_id: str = ""
    enrollment_id: str = ""
    user_id: str = ""
    company_id: str = "default"
    
    # Attempt details
    attempt_number: int = 1
    started_at: datetime = field(default_factory=datetime.utcnow)
    submitted_at: Optional[datetime] = None
    time_spent_minutes: int = 0
    
    # Answers and scoring
    answers: Dict[str, Any] = field(default_factory=dict)  # question_id -> answer
    score: float = 0.0
    points_earned: int = 0
    total_points: int = 0
    passed: bool = False
    
    def to_dict(self) -> dict:
        return {
            'attempt_id': self.attempt_id,
            'quiz_id': self.quiz_id,
            'enrollment_id': self.enrollment_id,
            'user_id': self.user_id,
            'company_id': self.company_id,
            'attempt_number': self.attempt_number,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'submitted_at': self.submitted_at.isoformat() if self.submitted_at else None,
            'time_spent_minutes': self.time_spent_minutes,
            'answers': self.answers,
            'score': self.score,
            'points_earned': self.points_earned,
            'total_points': self.total_points,
            'passed': self.passed,
        }


@dataclass
class ResourceLibraryItem:
    """
    Supplementary learning resource in the centralized library.
    """
    resource_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    company_id: str = "default"
    
    title: str = ""
    description: str = ""
    
    # Content
    resource_type: str = "document"  # document, video, link, manual
    file_url: str = ""
    file_size_bytes: int = 0
    mime_type: str = ""
    
    # Categorization
    category: str = ""
    tags: List[str] = field(default_factory=list)
    related_courses: List[str] = field(default_factory=list)
    
    # Access
    is_public: bool = True
    allowed_roles: List[str] = field(default_factory=list)
    
    # Metadata
    uploaded_by: str = ""
    uploaded_at: datetime = field(default_factory=datetime.utcnow)
    download_count: int = 0
    
    def to_dict(self) -> dict:
        return {
            'resource_id': self.resource_id,
            'company_id': self.company_id,
            'title': self.title,
            'description': self.description,
            'resource_type': self.resource_type,
            'file_url': self.file_url,
            'file_size_bytes': self.file_size_bytes,
            'mime_type': self.mime_type,
            'category': self.category,
            'tags': self.tags,
            'related_courses': self.related_courses,
            'is_public': self.is_public,
            'allowed_roles': self.allowed_roles,
            'uploaded_by': self.uploaded_by,
            'uploaded_at': self.uploaded_at.isoformat() if self.uploaded_at else None,
            'download_count': self.download_count,
        }


@dataclass
class GamificationProfile:
    """
    Learner gamification stats - points, badges, leaderboard position.
    """
    user_id: str = ""
    company_id: str = "default"
    
    # Points
    total_points: int = 0
    points_this_month: int = 0
    points_this_quarter: int = 0
    
    # Badges earned
    badges: List[Dict[str, Any]] = field(default_factory=list)
    # Badge format: {"badge_id": "...", "name": "...", "icon": "...", "earned_at": "..."}
    
    # Achievements
    courses_completed: int = 0
    paths_completed: int = 0
    quizzes_passed: int = 0
    perfect_scores: int = 0
    
    # Streaks
    current_streak_days: int = 0
    longest_streak_days: int = 0
    last_activity_date: Optional[date] = None
    
    # Leaderboard
    department_rank: int = 0
    company_rank: int = 0
    
    def to_dict(self) -> dict:
        return {
            'user_id': self.user_id,
            'company_id': self.company_id,
            'total_points': self.total_points,
            'points_this_month': self.points_this_month,
            'points_this_quarter': self.points_this_quarter,
            'badges': self.badges,
            'courses_completed': self.courses_completed,
            'paths_completed': self.paths_completed,
            'quizzes_passed': self.quizzes_passed,
            'perfect_scores': self.perfect_scores,
            'current_streak_days': self.current_streak_days,
            'longest_streak_days': self.longest_streak_days,
            'last_activity_date': self.last_activity_date.isoformat() if self.last_activity_date else None,
            'department_rank': self.department_rank,
            'company_rank': self.company_rank,
        }


@dataclass
class SkillMatrix:
    """
    Employee skill assessment linked to training recommendations.
    Used for performance review integration.
    """
    user_id: str = ""
    company_id: str = "default"
    
    # Skills and proficiency (1-5 scale)
    skills: Dict[str, int] = field(default_factory=dict)
    # e.g., {"Python": 4, "Leadership": 2, "Public Speaking": 3}
    
    # Gaps identified (from performance reviews)
    skill_gaps: List[str] = field(default_factory=list)
    
    # Recommended courses based on gaps
    recommended_courses: List[str] = field(default_factory=list)
    
    # Career path progress
    current_role: str = ""
    target_role: str = ""
    role_readiness_percent: float = 0.0
    
    # Last updated
    last_review_date: Optional[date] = None
    reviewed_by: str = ""
    
    def to_dict(self) -> dict:
        return {
            'user_id': self.user_id,
            'company_id': self.company_id,
            'skills': self.skills,
            'skill_gaps': self.skill_gaps,
            'recommended_courses': self.recommended_courses,
            'current_role': self.current_role,
            'target_role': self.target_role,
            'role_readiness_percent': self.role_readiness_percent,
            'last_review_date': self.last_review_date.isoformat() if self.last_review_date else None,
            'reviewed_by': self.reviewed_by,
        }


# ══════════════════════════════════════════════════════════════════════
# Badge Definitions (Gamification)
# ══════════════════════════════════════════════════════════════════════

BADGES = {
    'first_course': {
        'name': 'First Steps',
        'description': 'Completed your first course',
        'icon': 'bi-trophy',
        'points': 50,
    },
    'quick_learner': {
        'name': 'Quick Learner',
        'description': 'Completed a course in under 1 hour',
        'icon': 'bi-lightning',
        'points': 100,
    },
    'perfect_score': {
        'name': 'Perfect Score',
        'description': 'Scored 100% on a quiz',
        'icon': 'bi-star-fill',
        'points': 200,
    },
    'streak_7': {
        'name': 'Week Warrior',
        'description': '7-day learning streak',
        'icon': 'bi-fire',
        'points': 150,
    },
    'streak_30': {
        'name': 'Monthly Master',
        'description': '30-day learning streak',
        'icon': 'bi-calendar-check',
        'points': 500,
    },
    'path_completer': {
        'name': 'Path Finder',
        'description': 'Completed a learning path',
        'icon': 'bi-signpost-2',
        'points': 300,
    },
    'compliance_champion': {
        'name': 'Compliance Champion',
        'description': 'All compliance training up to date',
        'icon': 'bi-shield-check',
        'points': 250,
    },
    'mentor': {
        'name': 'Mentor',
        'description': 'Helped 5 colleagues complete training',
        'icon': 'bi-people',
        'points': 400,
    },
    'top_10': {
        'name': 'Top 10',
        'description': 'Reached top 10 on company leaderboard',
        'icon': 'bi-award',
        'points': 500,
    },
}


# ══════════════════════════════════════════════════════════════════════
# Default Onboarding Courses (Auto-assigned to new hires)
# ══════════════════════════════════════════════════════════════════════

DEFAULT_ONBOARDING_COURSES = [
    {
        'title': 'Company Policies & Culture',
        'description': 'Introduction to our company values, policies, and workplace culture.',
        'category': 'Onboarding',
        'is_compliance_required': True,
        'compliance_category': 'policy',
        'duration_minutes': 45,
    },
    {
        'title': 'Workplace Safety Essentials',
        'description': 'Essential safety protocols and emergency procedures.',
        'category': 'Onboarding',
        'is_compliance_required': True,
        'compliance_category': 'safety',
        'duration_minutes': 60,
    },
    {
        'title': 'IT Security & Data Protection',
        'description': 'Cybersecurity best practices and data handling guidelines.',
        'category': 'Onboarding',
        'is_compliance_required': True,
        'compliance_category': 'security',
        'duration_minutes': 30,
    },
    {
        'title': 'Anti-Harassment & Ethics',
        'description': 'Creating a respectful workplace and ethical conduct.',
        'category': 'Onboarding',
        'is_compliance_required': True,
        'compliance_category': 'ethics',
        'duration_minutes': 45,
    },
]
