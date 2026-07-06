"""
WebSocket-enhanced ConflictDetectorService example.
Shows how to add event emissions to conflict detection and resolution.

This file demonstrates the pattern - integrate into existing services/conflict_detector.py
"""

import logging
from typing import Dict, Any, List, Optional

from loregarden.websocket_events import (
    emit_conflict_detected,
    emit_conflict_resolved,
    emit_error,
)

logger = logging.getLogger(__name__)


# EXAMPLE 1: Enhanced detect_conflicts with event emission
def detect_conflicts_ws(
    self,  # self from ConflictDetectorService
    worktree_id: str,
    run_id: str,
) -> Dict[str, Any]:
    """
    Detect merge conflicts in a worktree.

    Events emitted:
    - conflict_detected: When conflicts are found
    """
    try:
        logger.info(f'Detecting conflicts in worktree {worktree_id}')

        # Existing detection logic would go here
        # conflicts = self._detect_merge_conflicts(worktree_id)
        # severity = self._assess_severity(conflicts)
        # preview = self._build_preview(conflicts, severity)

        conflicts = []  # placeholder
        preview = {
            'conflicting_files': [],
            'total_conflicts': 0,
            'auto_mergeable_count': 0,
            'severity': 'low',
        }

        # NEW: Emit conflict detection event only if conflicts found
        if conflicts:
            try:
                emit_conflict_detected(
                    worktree_id=worktree_id,
                    run_id=run_id,
                    conflicts=conflicts,
                    preview=preview,
                    severity=preview['severity'],
                )

                logger.info(
                    f'Emitted conflict_detected for {worktree_id}',
                    extra={
                        'conflict_count': len(conflicts),
                        'severity': preview['severity']
                    }
                )
            except Exception as e:
                logger.warning(f'Failed to emit conflict_detected: {e}')
        else:
            logger.debug(f'No conflicts detected in {worktree_id}')

        return {
            'worktree_id': worktree_id,
            'run_id': run_id,
            'conflicts': conflicts,
            'merge_preview': preview,
            'timestamp': None,  # would be set to now()
        }

    except Exception as e:
        logger.error(f'Error detecting conflicts: {e}', exc_info=True)

        # NEW: Emit error event
        try:
            emit_error(
                target_room=f'worktree:{worktree_id}',
                message=f'Failed to detect conflicts: {str(e)}',
                code='CONFLICT_DETECTION_ERROR',
                context={'worktree_id': worktree_id, 'run_id': run_id}
            )
        except Exception as emit_err:
            logger.warning(f'Failed to emit error: {emit_err}')

        raise


# EXAMPLE 2: Enhanced resolve_conflicts with event emission
def resolve_conflicts_ws(
    self,  # self from ConflictDetectorService
    worktree_id: str,
    run_id: str,
    strategy: str = 'auto',
) -> Dict[str, Any]:
    """
    Resolve detected conflicts in a worktree.

    Events emitted:
    - conflict_resolved: When resolution succeeds
    """
    try:
        logger.info(
            f'Resolving conflicts in {worktree_id} using {strategy} strategy'
        )

        # Existing resolution logic would go here
        # result = self._apply_resolution(worktree_id, strategy)
        # resolved_files = result['resolved_files']
        # remaining_conflicts = result['remaining_conflicts']

        resolved = True  # placeholder
        remaining_conflicts = []  # placeholder

        if resolved:
            logger.info(f'Conflicts resolved in {worktree_id}')

            # NEW: Emit conflict resolution event
            try:
                emit_conflict_resolved(
                    worktree_id=worktree_id,
                    run_id=run_id,
                )

                logger.info(f'Emitted conflict_resolved for {worktree_id}')
            except Exception as e:
                logger.warning(f'Failed to emit conflict_resolved: {e}')

            return {
                'worktree_id': worktree_id,
                'run_id': run_id,
                'status': 'resolved',
                'remaining_conflicts': remaining_conflicts,
            }
        else:
            logger.warning(f'Could not resolve conflicts in {worktree_id}')

            # NEW: Emit error event (resolution failed)
            try:
                emit_error(
                    target_room=f'worktree:{worktree_id}',
                    message='Failed to resolve conflicts automatically',
                    code='CONFLICT_RESOLUTION_FAILED',
                    context={
                        'worktree_id': worktree_id,
                        'strategy': strategy,
                        'remaining': len(remaining_conflicts),
                    }
                )
            except Exception as e:
                logger.warning(f'Failed to emit error: {e}')

            raise Exception('Conflict resolution failed')

    except Exception as e:
        logger.error(f'Error resolving conflicts: {e}', exc_info=True)

        # NEW: Emit error event
        try:
            emit_error(
                target_room=f'worktree:{worktree_id}',
                message=f'Failed to resolve conflicts: {str(e)}',
                code='CONFLICT_RESOLUTION_ERROR',
                context={'worktree_id': worktree_id, 'strategy': strategy}
            )
        except Exception as emit_err:
            logger.warning(f'Failed to emit error: {emit_err}')

        raise


# EXAMPLE 3: Get conflict details (no event emission)
def get_conflict_details_ws(
    self,  # self from ConflictDetectorService
    worktree_id: str,
    run_id: str,
) -> Dict[str, Any]:
    """
    Get detailed conflict information.

    No event emissions here - this is called by status endpoint.
    Events are only emitted when state changes.
    """
    # Existing implementation
    # ... return conflict details ...
    pass


# EXAMPLE 4: Check auto-mergeable (no event emission)
def check_auto_mergeable_ws(
    self,  # self from ConflictDetectorService
    conflicts: List[Dict[str, Any]],
) -> bool:
    """
    Check if conflicts can be auto-merged.

    No event emissions - internal helper method.
    """
    # Existing implementation
    # ... analyze conflicts ...
    # return can_auto_merge
    pass


# EXAMPLE 5: Assess severity (no event emission)
def assess_severity_ws(
    self,  # self from ConflictDetectorService
    conflicts: List[Dict[str, Any]],
) -> str:
    """
    Assess conflict severity (low/medium/high).

    No event emissions - internal helper method.
    """
    # Existing implementation
    # ... analyze conflict types and counts ...
    # return severity
    pass


# SUMMARY OF INTEGRATION PATTERN
"""
Event Emission Pattern for ConflictDetectorService:

1. detect_conflicts():
   - Emit conflict_detected when conflicts found
   - Include: conflicts[], preview, severity
   - Silent if no conflicts (no event needed)

2. resolve_conflicts():
   - Emit conflict_resolved on success
   - Emit error event on failure
   - Include: strategy used, remaining conflicts

3. Status/detail methods (get_conflict_details, check_auto_mergeable):
   - No event emissions
   - Only called by polling clients via REST API
   - Events only for state changes

4. Error handling:
   - Always emit error events for failures
   - Include context (worktree_id, run_id, strategy)
   - Use code: 'CONFLICT_DETECTION_ERROR', 'CONFLICT_RESOLUTION_ERROR'

5. Granularity:
   - Emit when state changes (conflicts found/resolved)
   - Don't emit for queries (only state changes matter)
   - Include sufficient detail for client UI updates
"""
