"""
LMS Data Store - PostgreSQL Backend

Enterprise Learning Management System with HR integration.
Handles courses, enrollments, certificates, quizzes, and gamification.
"""
import json
import logging
import uuid
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

from db import get_cursor, get_conn, get_tenant_cursor

logger = logging.getLogger(__name__)


class LMSDataStore:
    """PostgreSQL-backed LMS data store with full CRUD operations."""

    def __init__(self):
        self._ensure_tables()

    def _ensure_tables(self):
        """Create LMS tables if they don't exist."""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS lms_courses (
                            course_id VARCHAR(64) PRIMARY KEY,
                            company_id VARCHAR(64) DEFAULT 'default',
                            title VARCHAR(255) NOT NULL,
                            description TEXT,
                            short_description VARCHAR(500),
                            thumbnail_url VARCHAR(500),
                            content_type VARCHAR(50) DEFAULT 'text',
                            content_url TEXT,
                            duration_minutes INT DEFAULT 0,
                            category VARCHAR(100),
                            tags JSONB DEFAULT '[]',
                            skill_level VARCHAR(50) DEFAULT 'beginner',
                            language VARCHAR(10) DEFAULT 'en',
                            prerequisites JSONB DEFAULT '[]',
                            passing_score INT DEFAULT 70,
                            max_attempts INT DEFAULT 3,
                            is_compliance_required BOOLEAN DEFAULT FALSE,
                            compliance_category VARCHAR(100),
                            validity_period_days INT DEFAULT 365,
                            status VARCHAR(50) DEFAULT 'draft',
                            created_by VARCHAR(100),
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            published_at TIMESTAMP,
                            total_enrollments INT DEFAULT 0,
                            completion_rate DECIMAL(5,2) DEFAULT 0,
                            average_score DECIMAL(5,2) DEFAULT 0,
                            average_time_minutes DECIMAL(10,2) DEFAULT 0
                        );
                        
                        CREATE TABLE IF NOT EXISTS lms_learning_paths (
                            path_id VARCHAR(64) PRIMARY KEY,
                            company_id VARCHAR(64) DEFAULT 'default',
                            title VARCHAR(255) NOT NULL,
                            description TEXT,
                            thumbnail_url VARCHAR(500),
                            course_ids JSONB DEFAULT '[]',
                            target_roles JSONB DEFAULT '[]',
                            target_departments JSONB DEFAULT '[]',
                            grants_certification BOOLEAN DEFAULT FALSE,
                            certification_name VARCHAR(255),
                            certification_validity_days INT DEFAULT 365,
                            status VARCHAR(50) DEFAULT 'draft',
                            created_by VARCHAR(100),
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            total_enrollments INT DEFAULT 0,
                            completion_rate DECIMAL(5,2) DEFAULT 0,
                            average_completion_days DECIMAL(10,2) DEFAULT 0
                        );
                        
                        CREATE TABLE IF NOT EXISTS lms_enrollments (
                            enrollment_id VARCHAR(64) PRIMARY KEY,
                            company_id VARCHAR(64) DEFAULT 'default',
                            user_id VARCHAR(64) NOT NULL,
                            username VARCHAR(100),
                            course_id VARCHAR(64),
                            learning_path_id VARCHAR(64),
                            assignment_type VARCHAR(50) DEFAULT 'self_enrolled',
                            assigned_by VARCHAR(100),
                            assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            due_date DATE,
                            status VARCHAR(50) DEFAULT 'enrolled',
                            progress_percent DECIMAL(5,2) DEFAULT 0,
                            score DECIMAL(5,2),
                            attempts INT DEFAULT 0,
                            started_at TIMESTAMP,
                            completed_at TIMESTAMP,
                            last_accessed_at TIMESTAMP,
                            time_spent_minutes INT DEFAULT 0,
                            course_progress JSONB DEFAULT '{}'
                        );
                        
                        CREATE TABLE IF NOT EXISTS lms_certificates (
                            certificate_id VARCHAR(64) PRIMARY KEY,
                            company_id VARCHAR(64) DEFAULT 'default',
                            user_id VARCHAR(64) NOT NULL,
                            username VARCHAR(100),
                            full_name VARCHAR(255),
                            course_id VARCHAR(64),
                            learning_path_id VARCHAR(64),
                            course_title VARCHAR(255),
                            certificate_number VARCHAR(100) UNIQUE,
                            certificate_name VARCHAR(255),
                            issue_date DATE DEFAULT CURRENT_DATE,
                            expiry_date DATE,
                            score DECIMAL(5,2) DEFAULT 0,
                            status VARCHAR(50) DEFAULT 'valid',
                            pdf_url TEXT,
                            is_compliance_certificate BOOLEAN DEFAULT FALSE,
                            compliance_category VARCHAR(100),
                            attached_to_personnel_file BOOLEAN DEFAULT FALSE,
                            issued_by VARCHAR(100),
                            revoked_by VARCHAR(100),
                            revoked_at TIMESTAMP,
                            revocation_reason TEXT
                        );
                        
                        CREATE TABLE IF NOT EXISTS lms_quizzes (
                            quiz_id VARCHAR(64) PRIMARY KEY,
                            course_id VARCHAR(64),
                            company_id VARCHAR(64) DEFAULT 'default',
                            title VARCHAR(255) NOT NULL,
                            description TEXT,
                            passing_score INT DEFAULT 70,
                            time_limit_minutes INT DEFAULT 0,
                            randomize_questions BOOLEAN DEFAULT FALSE,
                            show_correct_answers BOOLEAN DEFAULT TRUE,
                            max_attempts INT DEFAULT 3,
                            questions JSONB DEFAULT '[]',
                            total_points INT DEFAULT 0,
                            created_by VARCHAR(100),
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        );
                        
                        CREATE TABLE IF NOT EXISTS lms_quiz_attempts (
                            attempt_id VARCHAR(64) PRIMARY KEY,
                            quiz_id VARCHAR(64) NOT NULL,
                            enrollment_id VARCHAR(64),
                            user_id VARCHAR(64) NOT NULL,
                            company_id VARCHAR(64) DEFAULT 'default',
                            attempt_number INT DEFAULT 1,
                            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            submitted_at TIMESTAMP,
                            time_spent_minutes INT DEFAULT 0,
                            answers JSONB DEFAULT '{}',
                            score DECIMAL(5,2) DEFAULT 0,
                            points_earned INT DEFAULT 0,
                            total_points INT DEFAULT 0,
                            passed BOOLEAN DEFAULT FALSE
                        );
                        
                        CREATE TABLE IF NOT EXISTS lms_resources (
                            resource_id VARCHAR(64) PRIMARY KEY,
                            company_id VARCHAR(64) DEFAULT 'default',
                            title VARCHAR(255) NOT NULL,
                            description TEXT,
                            resource_type VARCHAR(50) DEFAULT 'document',
                            file_url TEXT,
                            file_size_bytes BIGINT DEFAULT 0,
                            mime_type VARCHAR(100),
                            category VARCHAR(100),
                            tags JSONB DEFAULT '[]',
                            related_courses JSONB DEFAULT '[]',
                            is_public BOOLEAN DEFAULT TRUE,
                            allowed_roles JSONB DEFAULT '[]',
                            uploaded_by VARCHAR(100),
                            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            download_count INT DEFAULT 0
                        );
                        
                        CREATE TABLE IF NOT EXISTS lms_gamification (
                            user_id VARCHAR(64) PRIMARY KEY,
                            company_id VARCHAR(64) DEFAULT 'default',
                            total_points INT DEFAULT 0,
                            points_this_month INT DEFAULT 0,
                            points_this_quarter INT DEFAULT 0,
                            badges JSONB DEFAULT '[]',
                            courses_completed INT DEFAULT 0,
                            paths_completed INT DEFAULT 0,
                            quizzes_passed INT DEFAULT 0,
                            perfect_scores INT DEFAULT 0,
                            current_streak_days INT DEFAULT 0,
                            longest_streak_days INT DEFAULT 0,
                            last_activity_date DATE,
                            department_rank INT DEFAULT 0,
                            company_rank INT DEFAULT 0
                        );
                        
                        CREATE TABLE IF NOT EXISTS lms_skill_matrix (
                            user_id VARCHAR(64) PRIMARY KEY,
                            company_id VARCHAR(64) DEFAULT 'default',
                            skills JSONB DEFAULT '{}',
                            skill_gaps JSONB DEFAULT '[]',
                            recommended_courses JSONB DEFAULT '[]',
                            current_role VARCHAR(255),
                            target_role VARCHAR(255),
                            role_readiness_percent DECIMAL(5,2) DEFAULT 0,
                            last_review_date DATE,
                            reviewed_by VARCHAR(100)
                        );
                        
                        -- Indexes for performance
                        CREATE INDEX IF NOT EXISTS idx_lms_courses_company ON lms_courses(company_id);
                        CREATE INDEX IF NOT EXISTS idx_lms_courses_status ON lms_courses(status);
                        CREATE INDEX IF NOT EXISTS idx_lms_courses_category ON lms_courses(category);
                        CREATE INDEX IF NOT EXISTS idx_lms_enrollments_user ON lms_enrollments(user_id);
                        CREATE INDEX IF NOT EXISTS idx_lms_enrollments_course ON lms_enrollments(course_id);
                        CREATE INDEX IF NOT EXISTS idx_lms_enrollments_status ON lms_enrollments(status);
                        CREATE INDEX IF NOT EXISTS idx_lms_certificates_user ON lms_certificates(user_id);
                        CREATE INDEX IF NOT EXISTS idx_lms_certificates_expiry ON lms_certificates(expiry_date);
                    """)
                    conn.commit()
        except Exception as e:
            logger.warning("LMS tables check: %s", e)

    # ══════════════════════════════════════════════════════════════════════
    # COURSES
    # ══════════════════════════════════════════════════════════════════════

    def create_course(self, data: dict) -> Optional[str]:
        """Create a new course."""
        course_id = data.get('course_id') or str(uuid.uuid4())
        cid = data.get('company_id', 'default')
        try:
            with get_cursor() as cur:
                cur.execute("""
                    INSERT INTO lms_courses 
                    (course_id, company_id, title, description, short_description,
                     thumbnail_url, content_type, content_url, duration_minutes,
                     category, tags, skill_level, language, prerequisites,
                     passing_score, max_attempts, is_compliance_required,
                     compliance_category, validity_period_days, status, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING course_id
                """, (
                    course_id, cid,
                    data.get('title', ''),
                    data.get('description', ''),
                    data.get('short_description', ''),
                    data.get('thumbnail_url', ''),
                    data.get('content_type', 'text'),
                    data.get('content_url', ''),
                    int(data.get('duration_minutes', 0)),
                    data.get('category', ''),
                    json.dumps(data.get('tags', [])),
                    data.get('skill_level', 'beginner'),
                    data.get('language', 'en'),
                    json.dumps(data.get('prerequisites', [])),
                    int(data.get('passing_score', 70)),
                    int(data.get('max_attempts', 3)),
                    data.get('is_compliance_required', False),
                    data.get('compliance_category', ''),
                    int(data.get('validity_period_days', 365)),
                    data.get('status', 'draft'),
                    data.get('created_by', ''),
                ))
                row = cur.fetchone()
                return row['course_id'] if row else course_id
        except Exception as e:
            logger.error("create_course failed: %s", e)
            return None

    def get_course(self, course_id: str, company_id: str = None) -> Optional[dict]:
        """Get a single course by ID."""
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute("SELECT * FROM lms_courses WHERE course_id = %s", (course_id,))
                row = cur.fetchone()
                if row:
                    d = dict(row)
                    d['tags'] = d.get('tags') or []
                    d['prerequisites'] = d.get('prerequisites') or []
                    return d
                return None
        except Exception as e:
            logger.error("get_course failed: %s", e)
            return None

    def get_courses(self, company_id: str = None, status: str = None, 
                    category: str = None, is_compliance: bool = None) -> List[dict]:
        """Get all courses with optional filters."""
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                sql = "SELECT * FROM lms_courses WHERE company_id = %s"
                params = [cid]
                
                if status:
                    sql += " AND status = %s"
                    params.append(status)
                if category:
                    sql += " AND category = %s"
                    params.append(category)
                if is_compliance is not None:
                    sql += " AND is_compliance_required = %s"
                    params.append(is_compliance)
                
                sql += " ORDER BY created_at DESC"
                cur.execute(sql, params)
                
                courses = []
                for row in cur.fetchall():
                    d = dict(row)
                    d['tags'] = d.get('tags') or []
                    d['prerequisites'] = d.get('prerequisites') or []
                    courses.append(d)
                return courses
        except Exception as e:
            logger.error("get_courses failed: %s", e)
            return []

    def update_course(self, course_id: str, data: dict) -> bool:
        """Update an existing course."""
        try:
            with get_cursor() as cur:
                updates = []
                params = []
                
                for field in ['title', 'description', 'short_description', 'thumbnail_url',
                              'content_type', 'content_url', 'duration_minutes', 'category',
                              'skill_level', 'language', 'passing_score', 'max_attempts',
                              'is_compliance_required', 'compliance_category', 
                              'validity_period_days', 'status']:
                    if field in data:
                        updates.append(f"{field} = %s")
                        params.append(data[field])
                
                if 'tags' in data:
                    updates.append("tags = %s")
                    params.append(json.dumps(data['tags']))
                if 'prerequisites' in data:
                    updates.append("prerequisites = %s")
                    params.append(json.dumps(data['prerequisites']))
                
                updates.append("updated_at = CURRENT_TIMESTAMP")
                
                if data.get('status') == 'published':
                    updates.append("published_at = CURRENT_TIMESTAMP")
                
                if not updates:
                    return True
                
                params.append(course_id)
                cur.execute(f"""
                    UPDATE lms_courses SET {', '.join(updates)}
                    WHERE course_id = %s
                """, params)
                return True
        except Exception as e:
            logger.error("update_course failed: %s", e)
            return False

    def delete_course(self, course_id: str) -> bool:
        """Delete a course."""
        try:
            with get_cursor() as cur:
                cur.execute("DELETE FROM lms_courses WHERE course_id = %s", (course_id,))
                return True
        except Exception as e:
            logger.error("delete_course failed: %s", e)
            return False

    def get_course_categories(self, company_id: str = None) -> List[str]:
        """Get distinct course categories."""
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute("""
                    SELECT DISTINCT category FROM lms_courses 
                    WHERE company_id = %s AND category IS NOT NULL AND category != ''
                    ORDER BY category
                """, (cid,))
                return [row['category'] for row in cur.fetchall()]
        except Exception as e:
            logger.error("get_course_categories failed: %s", e)
            return ['Onboarding', 'Compliance', 'Technical', 'Leadership', 'Soft Skills']

    # ══════════════════════════════════════════════════════════════════════
    # LEARNING PATHS
    # ══════════════════════════════════════════════════════════════════════

    def create_learning_path(self, data: dict) -> Optional[str]:
        """Create a new learning path."""
        path_id = data.get('path_id') or str(uuid.uuid4())
        cid = data.get('company_id', 'default')
        try:
            with get_cursor() as cur:
                cur.execute("""
                    INSERT INTO lms_learning_paths
                    (path_id, company_id, title, description, thumbnail_url,
                     course_ids, target_roles, target_departments,
                     grants_certification, certification_name, 
                     certification_validity_days, status, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING path_id
                """, (
                    path_id, cid,
                    data.get('title', ''),
                    data.get('description', ''),
                    data.get('thumbnail_url', ''),
                    json.dumps(data.get('course_ids', [])),
                    json.dumps(data.get('target_roles', [])),
                    json.dumps(data.get('target_departments', [])),
                    data.get('grants_certification', False),
                    data.get('certification_name', ''),
                    int(data.get('certification_validity_days', 365)),
                    data.get('status', 'draft'),
                    data.get('created_by', ''),
                ))
                row = cur.fetchone()
                return row['path_id'] if row else path_id
        except Exception as e:
            logger.error("create_learning_path failed: %s", e)
            return None

    def get_learning_path(self, path_id: str, company_id: str = None) -> Optional[dict]:
        """Get a learning path by ID."""
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute("SELECT * FROM lms_learning_paths WHERE path_id = %s", (path_id,))
                row = cur.fetchone()
                if row:
                    d = dict(row)
                    d['course_ids'] = d.get('course_ids') or []
                    d['target_roles'] = d.get('target_roles') or []
                    d['target_departments'] = d.get('target_departments') or []
                    return d
                return None
        except Exception as e:
            logger.error("get_learning_path failed: %s", e)
            return None

    def get_learning_paths(self, company_id: str = None, status: str = None) -> List[dict]:
        """Get all learning paths."""
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                sql = "SELECT * FROM lms_learning_paths WHERE company_id = %s"
                params = [cid]
                if status:
                    sql += " AND status = %s"
                    params.append(status)
                sql += " ORDER BY created_at DESC"
                cur.execute(sql, params)
                
                paths = []
                for row in cur.fetchall():
                    d = dict(row)
                    d['course_ids'] = d.get('course_ids') or []
                    d['target_roles'] = d.get('target_roles') or []
                    d['target_departments'] = d.get('target_departments') or []
                    paths.append(d)
                return paths
        except Exception as e:
            logger.error("get_learning_paths failed: %s", e)
            return []

    # ══════════════════════════════════════════════════════════════════════
    # ENROLLMENTS
    # ══════════════════════════════════════════════════════════════════════

    def enroll_user(self, data: dict) -> Optional[str]:
        """Enroll a user in a course or learning path."""
        enrollment_id = str(uuid.uuid4())
        cid = data.get('company_id', 'default')
        try:
            with get_cursor() as cur:
                # Check if already enrolled
                if data.get('course_id'):
                    cur.execute("""
                        SELECT enrollment_id FROM lms_enrollments 
                        WHERE user_id = %s AND course_id = %s AND status NOT IN ('completed', 'withdrawn')
                    """, (data['user_id'], data['course_id']))
                    if cur.fetchone():
                        logger.info("User already enrolled in course")
                        return None
                
                cur.execute("""
                    INSERT INTO lms_enrollments
                    (enrollment_id, company_id, user_id, username, course_id,
                     learning_path_id, assignment_type, assigned_by, due_date, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING enrollment_id
                """, (
                    enrollment_id, cid,
                    data.get('user_id', ''),
                    data.get('username', ''),
                    data.get('course_id'),
                    data.get('learning_path_id'),
                    data.get('assignment_type', 'self_enrolled'),
                    data.get('assigned_by', ''),
                    data.get('due_date'),
                    'enrolled',
                ))
                
                # Update course enrollment count
                if data.get('course_id'):
                    cur.execute("""
                        UPDATE lms_courses 
                        SET total_enrollments = total_enrollments + 1
                        WHERE course_id = %s
                    """, (data['course_id'],))
                
                row = cur.fetchone()
                return row['enrollment_id'] if row else enrollment_id
        except Exception as e:
            logger.error("enroll_user failed: %s", e)
            return None

    def get_user_enrollments(self, user_id: str, company_id: str = None,
                             status: str = None) -> List[dict]:
        """Get all enrollments for a user."""
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                sql = """
                    SELECT e.*, c.title as course_title, c.thumbnail_url, 
                           c.duration_minutes, c.category
                    FROM lms_enrollments e
                    LEFT JOIN lms_courses c ON e.course_id = c.course_id
                    WHERE e.user_id = %s AND e.company_id = %s
                """
                params = [user_id, cid]
                if status:
                    sql += " AND e.status = %s"
                    params.append(status)
                sql += " ORDER BY e.assigned_at DESC"
                cur.execute(sql, params)
                return [dict(row) for row in cur.fetchall()]
        except Exception as e:
            logger.error("get_user_enrollments failed: %s", e)
            return []

    def update_enrollment_progress(self, enrollment_id: str, progress: float,
                                   time_spent: int = 0, score: float = None) -> bool:
        """Update enrollment progress."""
        try:
            with get_cursor() as cur:
                updates = ["progress_percent = %s", "last_accessed_at = CURRENT_TIMESTAMP"]
                params = [progress]
                
                if time_spent:
                    updates.append("time_spent_minutes = time_spent_minutes + %s")
                    params.append(time_spent)
                
                if score is not None:
                    updates.append("score = %s")
                    params.append(score)
                
                # Auto-update status
                if progress >= 100:
                    updates.append("status = 'completed'")
                    updates.append("completed_at = CURRENT_TIMESTAMP")
                elif progress > 0:
                    updates.append("status = 'in_progress'")
                    updates.append("started_at = COALESCE(started_at, CURRENT_TIMESTAMP)")
                
                params.append(enrollment_id)
                cur.execute(f"""
                    UPDATE lms_enrollments SET {', '.join(updates)}
                    WHERE enrollment_id = %s
                """, params)
                return True
        except Exception as e:
            logger.error("update_enrollment_progress failed: %s", e)
            return False

    def complete_enrollment(self, enrollment_id: str, score: float = None) -> Optional[str]:
        """Mark enrollment as complete and generate certificate."""
        try:
            with get_cursor() as cur:
                # Get enrollment details
                cur.execute("""
                    SELECT e.*, c.title, c.is_compliance_required, 
                           c.compliance_category, c.validity_period_days,
                           c.passing_score
                    FROM lms_enrollments e
                    JOIN lms_courses c ON e.course_id = c.course_id
                    WHERE e.enrollment_id = %s
                """, (enrollment_id,))
                enrollment = cur.fetchone()
                
                if not enrollment:
                    return None
                
                enrollment = dict(enrollment)
                final_score = score or enrollment.get('score', 100)
                passed = final_score >= enrollment.get('passing_score', 70)
                
                # Update enrollment
                cur.execute("""
                    UPDATE lms_enrollments 
                    SET status = %s, score = %s, progress_percent = 100,
                        completed_at = CURRENT_TIMESTAMP
                    WHERE enrollment_id = %s
                """, ('completed' if passed else 'failed', final_score, enrollment_id))
                
                # Generate certificate if passed
                if passed:
                    cert_id = self._generate_certificate(enrollment, final_score)
                    return cert_id
                
                return None
        except Exception as e:
            logger.error("complete_enrollment failed: %s", e)
            return None

    def _generate_certificate(self, enrollment: dict, score: float) -> str:
        """Generate a certificate for completed training."""
        cert_id = str(uuid.uuid4())
        cert_number = f"CERT-{datetime.now().strftime('%Y%m%d')}-{cert_id[:8].upper()}"
        
        try:
            with get_cursor() as cur:
                expiry_date = None
                if enrollment.get('validity_period_days'):
                    expiry_date = date.today() + timedelta(days=enrollment['validity_period_days'])
                
                cur.execute("""
                    INSERT INTO lms_certificates
                    (certificate_id, company_id, user_id, username, course_id,
                     course_title, certificate_number, certificate_name,
                     issue_date, expiry_date, score, status,
                     is_compliance_certificate, compliance_category, issued_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    cert_id,
                    enrollment.get('company_id', 'default'),
                    enrollment['user_id'],
                    enrollment.get('username', ''),
                    enrollment.get('course_id'),
                    enrollment.get('title', ''),
                    cert_number,
                    f"Certificate of Completion - {enrollment.get('title', '')}",
                    date.today(),
                    expiry_date,
                    score,
                    'valid',
                    enrollment.get('is_compliance_required', False),
                    enrollment.get('compliance_category', ''),
                    'system',
                ))
                return cert_id
        except Exception as e:
            logger.error("_generate_certificate failed: %s", e)
            return cert_id

    # ══════════════════════════════════════════════════════════════════════
    # CERTIFICATES
    # ══════════════════════════════════════════════════════════════════════

    def get_user_certificates(self, user_id: str, company_id: str = None,
                              status: str = None) -> List[dict]:
        """Get all certificates for a user."""
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                sql = "SELECT * FROM lms_certificates WHERE user_id = %s AND company_id = %s"
                params = [user_id, cid]
                if status:
                    sql += " AND status = %s"
                    params.append(status)
                sql += " ORDER BY issue_date DESC"
                cur.execute(sql, params)
                return [dict(row) for row in cur.fetchall()]
        except Exception as e:
            logger.error("get_user_certificates failed: %s", e)
            return []

    def get_certificate(self, certificate_id: str) -> Optional[dict]:
        """Get a single certificate."""
        try:
            with get_cursor() as cur:
                cur.execute("SELECT * FROM lms_certificates WHERE certificate_id = %s", 
                           (certificate_id,))
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_certificate failed: %s", e)
            return None

    def get_expiring_certificates(self, days_ahead: int = 30, 
                                  company_id: str = None) -> List[dict]:
        """Get certificates expiring within N days."""
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute("""
                    SELECT c.*, u.full_name, u.email
                    FROM lms_certificates c
                    LEFT JOIN users u ON c.user_id = u.user_id
                    WHERE c.company_id = %s 
                      AND c.status = 'valid'
                      AND c.expiry_date IS NOT NULL
                      AND c.expiry_date <= CURRENT_DATE + INTERVAL '%s days'
                    ORDER BY c.expiry_date ASC
                """, (cid, days_ahead))
                return [dict(row) for row in cur.fetchall()]
        except Exception as e:
            logger.error("get_expiring_certificates failed: %s", e)
            return []

    def revoke_certificate(self, certificate_id: str, revoked_by: str, 
                          reason: str = '') -> bool:
        """Revoke a certificate."""
        try:
            with get_cursor() as cur:
                cur.execute("""
                    UPDATE lms_certificates 
                    SET status = 'revoked', revoked_by = %s, 
                        revoked_at = CURRENT_TIMESTAMP, revocation_reason = %s
                    WHERE certificate_id = %s
                """, (revoked_by, reason, certificate_id))
                return True
        except Exception as e:
            logger.error("revoke_certificate failed: %s", e)
            return False

    # ══════════════════════════════════════════════════════════════════════
    # QUIZZES
    # ══════════════════════════════════════════════════════════════════════

    def create_quiz(self, data: dict) -> Optional[str]:
        """Create a quiz for a course."""
        quiz_id = str(uuid.uuid4())
        cid = data.get('company_id', 'default')
        try:
            with get_cursor() as cur:
                questions = data.get('questions', [])
                total_points = sum(q.get('points', 10) for q in questions)
                
                cur.execute("""
                    INSERT INTO lms_quizzes
                    (quiz_id, course_id, company_id, title, description,
                     passing_score, time_limit_minutes, randomize_questions,
                     show_correct_answers, max_attempts, questions, 
                     total_points, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING quiz_id
                """, (
                    quiz_id,
                    data.get('course_id', ''),
                    cid,
                    data.get('title', ''),
                    data.get('description', ''),
                    int(data.get('passing_score', 70)),
                    int(data.get('time_limit_minutes', 0)),
                    data.get('randomize_questions', False),
                    data.get('show_correct_answers', True),
                    int(data.get('max_attempts', 3)),
                    json.dumps(questions),
                    total_points,
                    data.get('created_by', ''),
                ))
                row = cur.fetchone()
                return row['quiz_id'] if row else quiz_id
        except Exception as e:
            logger.error("create_quiz failed: %s", e)
            return None

    def get_quiz(self, quiz_id: str) -> Optional[dict]:
        """Get a quiz by ID."""
        try:
            with get_cursor() as cur:
                cur.execute("SELECT * FROM lms_quizzes WHERE quiz_id = %s", (quiz_id,))
                row = cur.fetchone()
                if row:
                    d = dict(row)
                    d['questions'] = d.get('questions') or []
                    return d
                return None
        except Exception as e:
            logger.error("get_quiz failed: %s", e)
            return None

    def submit_quiz_attempt(self, data: dict) -> dict:
        """Submit a quiz attempt and calculate score."""
        attempt_id = str(uuid.uuid4())
        try:
            # Get quiz details
            quiz = self.get_quiz(data['quiz_id'])
            if not quiz:
                return {'success': False, 'error': 'Quiz not found'}
            
            # Calculate score
            answers = data.get('answers', {})
            questions = quiz.get('questions', [])
            points_earned = 0
            total_points = quiz.get('total_points', 0)
            
            for q in questions:
                q_id = q.get('id')
                if q_id in answers:
                    correct = q.get('correct_answer')
                    given = answers[q_id]
                    if isinstance(correct, list):
                        if set(given) == set(correct):
                            points_earned += q.get('points', 10)
                    elif given == correct:
                        points_earned += q.get('points', 10)
            
            score = (points_earned / total_points * 100) if total_points > 0 else 0
            passed = score >= quiz.get('passing_score', 70)
            
            with get_cursor() as cur:
                cur.execute("""
                    INSERT INTO lms_quiz_attempts
                    (attempt_id, quiz_id, enrollment_id, user_id, company_id,
                     attempt_number, submitted_at, time_spent_minutes, answers,
                     score, points_earned, total_points, passed)
                    VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, %s, %s, %s, %s, %s, %s)
                """, (
                    attempt_id,
                    data['quiz_id'],
                    data.get('enrollment_id', ''),
                    data['user_id'],
                    data.get('company_id', 'default'),
                    data.get('attempt_number', 1),
                    data.get('time_spent_minutes', 0),
                    json.dumps(answers),
                    score,
                    points_earned,
                    total_points,
                    passed,
                ))
            
            return {
                'success': True,
                'attempt_id': attempt_id,
                'score': score,
                'points_earned': points_earned,
                'total_points': total_points,
                'passed': passed,
            }
        except Exception as e:
            logger.error("submit_quiz_attempt failed: %s", e)
            return {'success': False, 'error': str(e)}

    # ══════════════════════════════════════════════════════════════════════
    # GAMIFICATION
    # ══════════════════════════════════════════════════════════════════════

    def get_gamification_profile(self, user_id: str, company_id: str = None) -> dict:
        """Get or create gamification profile for a user."""
        cid = company_id or 'default'
        try:
            with get_cursor() as cur:
                cur.execute("""
                    SELECT * FROM lms_gamification WHERE user_id = %s AND company_id = %s
                """, (user_id, cid))
                row = cur.fetchone()
                
                if row:
                    d = dict(row)
                    d['badges'] = d.get('badges') or []
                    return d
                
                # Create new profile
                cur.execute("""
                    INSERT INTO lms_gamification (user_id, company_id)
                    VALUES (%s, %s)
                    ON CONFLICT (user_id) DO NOTHING
                """, (user_id, cid))
                
                return {
                    'user_id': user_id,
                    'company_id': cid,
                    'total_points': 0,
                    'badges': [],
                    'courses_completed': 0,
                    'current_streak_days': 0,
                }
        except Exception as e:
            logger.error("get_gamification_profile failed: %s", e)
            return {'user_id': user_id, 'total_points': 0, 'badges': []}

    def award_points(self, user_id: str, points: int, reason: str = '',
                    company_id: str = None) -> bool:
        """Award points to a user."""
        cid = company_id or 'default'
        try:
            with get_cursor() as cur:
                cur.execute("""
                    INSERT INTO lms_gamification (user_id, company_id, total_points, 
                                                  points_this_month, points_this_quarter)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE
                    SET total_points = lms_gamification.total_points + %s,
                        points_this_month = lms_gamification.points_this_month + %s,
                        points_this_quarter = lms_gamification.points_this_quarter + %s,
                        last_activity_date = CURRENT_DATE
                """, (user_id, cid, points, points, points, points, points, points))
                return True
        except Exception as e:
            logger.error("award_points failed: %s", e)
            return False

    def award_badge(self, user_id: str, badge_id: str, company_id: str = None) -> bool:
        """Award a badge to a user."""
        cid = company_id or 'default'
        from models.lms import BADGES
        
        badge_info = BADGES.get(badge_id)
        if not badge_info:
            return False
        
        try:
            with get_cursor() as cur:
                # Check if already has badge
                cur.execute("""
                    SELECT badges FROM lms_gamification WHERE user_id = %s
                """, (user_id,))
                row = cur.fetchone()
                
                if row:
                    badges = row['badges'] or []
                    if any(b.get('badge_id') == badge_id for b in badges):
                        return False  # Already has badge
                
                new_badge = {
                    'badge_id': badge_id,
                    'name': badge_info['name'],
                    'icon': badge_info['icon'],
                    'earned_at': datetime.utcnow().isoformat(),
                }
                
                cur.execute("""
                    UPDATE lms_gamification 
                    SET badges = badges || %s::jsonb,
                        total_points = total_points + %s
                    WHERE user_id = %s
                """, (json.dumps([new_badge]), badge_info.get('points', 0), user_id))
                return True
        except Exception as e:
            logger.error("award_badge failed: %s", e)
            return False

    def get_leaderboard(self, company_id: str = None, department: str = None,
                       limit: int = 10) -> List[dict]:
        """Get top learners leaderboard."""
        cid = company_id or 'default'
        try:
            with get_cursor() as cur:
                cur.execute("""
                    SELECT g.*, u.full_name, u.username
                    FROM lms_gamification g
                    LEFT JOIN users u ON g.user_id = u.user_id
                    WHERE g.company_id = %s
                    ORDER BY g.total_points DESC
                    LIMIT %s
                """, (cid, limit))
                
                results = []
                for i, row in enumerate(cur.fetchall(), 1):
                    d = dict(row)
                    d['rank'] = i
                    d['badges'] = d.get('badges') or []
                    results.append(d)
                return results
        except Exception as e:
            logger.error("get_leaderboard failed: %s", e)
            return []

    # ══════════════════════════════════════════════════════════════════════
    # HR INTEGRATION
    # ══════════════════════════════════════════════════════════════════════

    def auto_assign_onboarding(self, user_id: str, username: str = '',
                               company_id: str = None) -> List[str]:
        """Auto-assign onboarding courses to a new employee."""
        cid = company_id or 'default'
        enrollment_ids = []
        
        try:
            # Get all compliance-required onboarding courses
            courses = self.get_courses(
                company_id=cid,
                status='published',
                is_compliance=True
            )
            
            onboarding_courses = [c for c in courses 
                                  if c.get('category', '').lower() == 'onboarding']
            
            for course in onboarding_courses:
                eid = self.enroll_user({
                    'user_id': user_id,
                    'username': username,
                    'course_id': course['course_id'],
                    'company_id': cid,
                    'assignment_type': 'auto_onboarding',
                    'assigned_by': 'system',
                    'due_date': date.today() + timedelta(days=30),
                })
                if eid:
                    enrollment_ids.append(eid)
            
            return enrollment_ids
        except Exception as e:
            logger.error("auto_assign_onboarding failed: %s", e)
            return []

    def get_skill_matrix(self, user_id: str, company_id: str = None) -> dict:
        """Get employee skill matrix."""
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                cur.execute("""
                    SELECT * FROM lms_skill_matrix WHERE user_id = %s AND company_id = %s
                """, (user_id, cid))
                row = cur.fetchone()
                if row:
                    d = dict(row)
                    d['skills'] = d.get('skills') or {}
                    d['skill_gaps'] = d.get('skill_gaps') or []
                    d['recommended_courses'] = d.get('recommended_courses') or []
                    return d
                return {
                    'user_id': user_id,
                    'skills': {},
                    'skill_gaps': [],
                    'recommended_courses': [],
                }
        except Exception as e:
            logger.error("get_skill_matrix failed: %s", e)
            return {'user_id': user_id, 'skills': {}}

    def update_skill_matrix(self, user_id: str, data: dict) -> bool:
        """Update employee skill matrix (usually from performance review)."""
        cid = data.get('company_id', 'default')
        try:
            with get_cursor() as cur:
                cur.execute("""
                    INSERT INTO lms_skill_matrix 
                    (user_id, company_id, skills, skill_gaps, recommended_courses,
                     current_role, target_role, role_readiness_percent, 
                     last_review_date, reviewed_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE
                    SET skills = %s, skill_gaps = %s, recommended_courses = %s,
                        current_role = %s, target_role = %s, 
                        role_readiness_percent = %s,
                        last_review_date = %s, reviewed_by = %s
                """, (
                    user_id, cid,
                    json.dumps(data.get('skills', {})),
                    json.dumps(data.get('skill_gaps', [])),
                    json.dumps(data.get('recommended_courses', [])),
                    data.get('current_role', ''),
                    data.get('target_role', ''),
                    data.get('role_readiness_percent', 0),
                    data.get('last_review_date'),
                    data.get('reviewed_by', ''),
                    # For UPDATE
                    json.dumps(data.get('skills', {})),
                    json.dumps(data.get('skill_gaps', [])),
                    json.dumps(data.get('recommended_courses', [])),
                    data.get('current_role', ''),
                    data.get('target_role', ''),
                    data.get('role_readiness_percent', 0),
                    data.get('last_review_date'),
                    data.get('reviewed_by', ''),
                ))
                return True
        except Exception as e:
            logger.error("update_skill_matrix failed: %s", e)
            return False

    def recommend_courses_for_skill_gaps(self, user_id: str, 
                                         company_id: str = None) -> List[dict]:
        """Recommend courses based on skill gaps from performance reviews."""
        cid = company_id or 'default'
        skill_matrix = self.get_skill_matrix(user_id, cid)
        skill_gaps = skill_matrix.get('skill_gaps', [])
        
        if not skill_gaps:
            return []
        
        # Find courses that match skill gaps
        all_courses = self.get_courses(company_id=cid, status='published')
        recommended = []
        
        for course in all_courses:
            course_tags = [t.lower() for t in course.get('tags', [])]
            course_title = course.get('title', '').lower()
            
            for gap in skill_gaps:
                gap_lower = gap.lower()
                if gap_lower in course_tags or gap_lower in course_title:
                    recommended.append(course)
                    break
        
        return recommended

    # ══════════════════════════════════════════════════════════════════════
    # ANALYTICS & REPORTING
    # ══════════════════════════════════════════════════════════════════════

    def get_dashboard_stats(self, company_id: str = None) -> dict:
        """Get LMS dashboard statistics."""
        cid = company_id or 'default'
        try:
            with get_tenant_cursor(cid) as cur:
                stats = {}
                
                # Total courses
                cur.execute("""
                    SELECT COUNT(*) as total, 
                           SUM(CASE WHEN status = 'published' THEN 1 ELSE 0 END) as published
                    FROM lms_courses WHERE company_id = %s
                """, (cid,))
                row = cur.fetchone()
                stats['total_courses'] = row['total'] or 0
                stats['published_courses'] = row['published'] or 0
                
                # Total enrollments
                cur.execute("""
                    SELECT COUNT(*) as total,
                           SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                           SUM(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 END) as in_progress
                    FROM lms_enrollments WHERE company_id = %s
                """, (cid,))
                row = cur.fetchone()
                stats['total_enrollments'] = row['total'] or 0
                stats['completed_enrollments'] = row['completed'] or 0
                stats['in_progress_enrollments'] = row['in_progress'] or 0
                
                # Completion rate
                if stats['total_enrollments'] > 0:
                    stats['completion_rate'] = round(
                        stats['completed_enrollments'] / stats['total_enrollments'] * 100, 1
                    )
                else:
                    stats['completion_rate'] = 0
                
                # Certificates
                cur.execute("""
                    SELECT COUNT(*) as total,
                           SUM(CASE WHEN status = 'valid' THEN 1 ELSE 0 END) as valid,
                           SUM(CASE WHEN expiry_date <= CURRENT_DATE + INTERVAL '30 days' 
                                    AND status = 'valid' THEN 1 ELSE 0 END) as expiring_soon
                    FROM lms_certificates WHERE company_id = %s
                """, (cid,))
                row = cur.fetchone()
                stats['total_certificates'] = row['total'] or 0
                stats['valid_certificates'] = row['valid'] or 0
                stats['expiring_certificates'] = row['expiring_soon'] or 0
                
                # Compliance status
                cur.execute("""
                    SELECT 
                        COUNT(DISTINCT e.user_id) as total_users,
                        COUNT(DISTINCT CASE WHEN e.status = 'completed' 
                                            THEN e.user_id END) as compliant_users
                    FROM lms_enrollments e
                    JOIN lms_courses c ON e.course_id = c.course_id
                    WHERE c.is_compliance_required = TRUE AND c.company_id = %s
                """, (cid,))
                row = cur.fetchone()
                stats['compliance_total_users'] = row['total_users'] or 0
                stats['compliance_compliant_users'] = row['compliant_users'] or 0
                
                return stats
        except Exception as e:
            logger.error("get_dashboard_stats failed: %s", e)
            return {
                'total_courses': 0, 'published_courses': 0,
                'total_enrollments': 0, 'completion_rate': 0,
                'total_certificates': 0, 'expiring_certificates': 0,
            }

    def get_manager_team_report(self, manager_id: str, company_id: str = None) -> dict:
        """Get training report for a manager's team."""
        cid = company_id or 'default'
        try:
            # This would typically join with HR data to get team members
            # For now, return placeholder structure
            return {
                'team_members': [],
                'overall_completion_rate': 0,
                'skill_gaps': [],
                'overdue_training': [],
                'top_performers': [],
            }
        except Exception as e:
            logger.error("get_manager_team_report failed: %s", e)
            return {}


# Singleton instance
lms_store = LMSDataStore()
