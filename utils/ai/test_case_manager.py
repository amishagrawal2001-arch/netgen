"""
User-defined test case management
Allows users to create, edit, and manage custom test cases
"""

import json
import logging
from typing import Dict, List, Optional
from pathlib import Path
import sqlite3
from datetime import datetime

from .network_test_framework import TestCase, TestStatus

logger = logging.getLogger(__name__)


class UserTestCaseManager:
    """Manage user-defined test cases"""
    
    def __init__(self, db_path: str = "/opt/OSTG/user_test_cases.db"):
        self.db_path = db_path
        self._init_database()
    
    def _init_database(self):
        """Initialize user test cases database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_test_cases (
                test_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                category TEXT,
                test_function TEXT,
                parameters TEXT,
                expected_result TEXT,
                severity TEXT,
                vendor_specific TEXT,
                prerequisites TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_by TEXT,
                enabled BOOLEAN DEFAULT 1
            )
        """)
        
        conn.commit()
        conn.close()
    
    def create_test_case(self, test_case: TestCase, created_by: str = "user") -> bool:
        """Create a user-defined test case"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                INSERT INTO user_test_cases 
                (test_id, name, description, category, test_function, parameters,
                 expected_result, severity, vendor_specific, prerequisites, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                test_case.test_id,
                test_case.name,
                test_case.description,
                test_case.category,
                test_case.test_function,
                json.dumps(test_case.parameters),
                test_case.expected_result,
                test_case.severity,
                test_case.vendor_specific,
                json.dumps(test_case.prerequisites),
                created_by
            ))
            
            conn.commit()
            conn.close()
            logger.info(f"Created user test case: {test_case.test_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to create test case: {e}")
            conn.rollback()
            conn.close()
            return False
    
    def get_test_case(self, test_id: str) -> Optional[TestCase]:
        """Get a user-defined test case"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT test_id, name, description, category, test_function, parameters,
                   expected_result, severity, vendor_specific, prerequisites
            FROM user_test_cases
            WHERE test_id = ? AND enabled = 1
        """, (test_id,))
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return TestCase(
                test_id=row[0],
                name=row[1],
                description=row[2] or "",
                category=row[3] or "custom",
                test_function=row[4],
                parameters=json.loads(row[5]) if row[5] else {},
                expected_result=row[6],
                severity=row[7] or "medium",
                vendor_specific=row[8],
                prerequisites=json.loads(row[9]) if row[9] else []
            )
        return None
    
    def get_all_test_cases(self) -> List[TestCase]:
        """Get all user-defined test cases"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT test_id, name, description, category, test_function, parameters,
                   expected_result, severity, vendor_specific, prerequisites
            FROM user_test_cases
            WHERE enabled = 1
            ORDER BY created_at DESC
        """)
        
        test_cases = []
        for row in cursor.fetchall():
            test_cases.append(TestCase(
                test_id=row[0],
                name=row[1],
                description=row[2] or "",
                category=row[3] or "custom",
                test_function=row[4],
                parameters=json.loads(row[5]) if row[5] else {},
                expected_result=row[6],
                severity=row[7] or "medium",
                vendor_specific=row[8],
                prerequisites=json.loads(row[9]) if row[9] else []
            ))
        
        conn.close()
        return test_cases
    
    def update_test_case(self, test_id: str, test_case: TestCase) -> bool:
        """Update a user-defined test case"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                UPDATE user_test_cases
                SET name = ?, description = ?, category = ?, test_function = ?,
                    parameters = ?, expected_result = ?, severity = ?,
                    vendor_specific = ?, prerequisites = ?, updated_at = CURRENT_TIMESTAMP
                WHERE test_id = ?
            """, (
                test_case.name,
                test_case.description,
                test_case.category,
                test_case.test_function,
                json.dumps(test_case.parameters),
                test_case.expected_result,
                test_case.severity,
                test_case.vendor_specific,
                json.dumps(test_case.prerequisites),
                test_id
            ))
            
            conn.commit()
            conn.close()
            logger.info(f"Updated user test case: {test_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to update test case: {e}")
            conn.rollback()
            conn.close()
            return False
    
    def delete_test_case(self, test_id: str) -> bool:
        """Delete (disable) a user-defined test case"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                UPDATE user_test_cases
                SET enabled = 0, updated_at = CURRENT_TIMESTAMP
                WHERE test_id = ?
            """, (test_id,))
            
            conn.commit()
            conn.close()
            logger.info(f"Deleted user test case: {test_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete test case: {e}")
            conn.rollback()
            conn.close()
            return False
    
    def export_test_cases(self, file_path: str) -> bool:
        """Export user test cases to JSON file"""
        try:
            test_cases = self.get_all_test_cases()
            export_data = {
                "exported_at": datetime.now().isoformat(),
                "test_cases": [{
                    "test_id": tc.test_id,
                    "name": tc.name,
                    "description": tc.description,
                    "category": tc.category,
                    "test_function": tc.test_function,
                    "parameters": tc.parameters,
                    "expected_result": tc.expected_result,
                    "severity": tc.severity,
                    "vendor_specific": tc.vendor_specific,
                    "prerequisites": tc.prerequisites
                } for tc in test_cases]
            }
            
            with open(file_path, 'w') as f:
                json.dump(export_data, f, indent=2)
            
            logger.info(f"Exported {len(test_cases)} test cases to {file_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to export test cases: {e}")
            return False
    
    def import_test_cases(self, file_path: str) -> int:
        """Import test cases from JSON file"""
        try:
            with open(file_path, 'r') as f:
                import_data = json.load(f)
            
            imported_count = 0
            for tc_data in import_data.get("test_cases", []):
                test_case = TestCase(
                    test_id=tc_data["test_id"],
                    name=tc_data["name"],
                    description=tc_data.get("description", ""),
                    category=tc_data.get("category", "custom"),
                    test_function=tc_data.get("test_function"),
                    parameters=tc_data.get("parameters", {}),
                    expected_result=tc_data.get("expected_result"),
                    severity=tc_data.get("severity", "medium"),
                    vendor_specific=tc_data.get("vendor_specific"),
                    prerequisites=tc_data.get("prerequisites", [])
                )
                
                if self.create_test_case(test_case):
                    imported_count += 1
            
            logger.info(f"Imported {imported_count} test cases from {file_path}")
            return imported_count
        except Exception as e:
            logger.error(f"Failed to import test cases: {e}")
            return 0




