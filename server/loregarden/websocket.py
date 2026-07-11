"""
WebSocket server for real-time parallel execution updates.
Uses Flask-SocketIO for event-driven communication.
"""

import logging
from datetime import datetime
from typing import Any

from flask import request
from flask_socketio import SocketIO, emit, join_room, leave_room

logger = logging.getLogger(__name__)


class WebSocketServer:
    """Manages WebSocket connections and events for parallel execution."""

    def __init__(self, socketio: SocketIO):
        self.socketio = socketio
        self.connected_users: dict[str, str] = {}  # sid -> user_id
        self.subscriptions: dict[str, list[str]] = {}  # room -> [sids]

    def initialize_handlers(self):
        """Register all WebSocket event handlers."""

        @self.socketio.on('connect')
        def handle_connect():
            """Handle client connection."""
            sid = request.sid
            user_id = request.args.get('user_id', 'anonymous')
            self.connected_users[sid] = user_id

            logger.info(
                f'Client connected: {sid} (user: {user_id})',
                extra={'sid': sid, 'user_id': user_id}
            )

            emit('connection_established', {
                'timestamp': datetime.utcnow().isoformat(),
                'server_time': datetime.utcnow().isoformat(),
            })

        @self.socketio.on('disconnect')
        def handle_disconnect():
            """Handle client disconnection."""
            sid = request.sid
            user_id = self.connected_users.pop(sid, 'unknown')

            logger.info(
                f'Client disconnected: {sid} (user: {user_id})',
                extra={'sid': sid, 'user_id': user_id}
            )

        @self.socketio.on('join_workspace')
        def handle_join_workspace(data):
            """Subscribe to execution updates for a workspace."""
            sid = request.sid
            workspace_id = data.get('workspaceId')
            user_id = self.connected_users.get(sid, 'anonymous')

            if not workspace_id:
                emit('error', {
                    'message': 'workspaceId required',
                    'code': 'INVALID_REQUEST',
                    'timestamp': datetime.utcnow().isoformat(),
                })
                return

            room = f'workspace:{workspace_id}'
            join_room(room)

            if room not in self.subscriptions:
                self.subscriptions[room] = []
            self.subscriptions[room].append(sid)

            logger.info(
                f'User {user_id} joined workspace {workspace_id}',
                extra={'sid': sid, 'workspace_id': workspace_id}
            )

            emit('workspace_joined', {
                'workspaceId': workspace_id,
                'timestamp': datetime.utcnow().isoformat(),
            })

        @self.socketio.on('leave_workspace')
        def handle_leave_workspace(data):
            """Unsubscribe from execution updates."""
            sid = request.sid
            workspace_id = data.get('workspaceId')
            user_id = self.connected_users.get(sid, 'anonymous')

            if workspace_id:
                room = f'workspace:{workspace_id}'
                leave_room(room)

                if room in self.subscriptions:
                    self.subscriptions[room] = [
                        s for s in self.subscriptions[room] if s != sid
                    ]

                logger.info(
                    f'User {user_id} left workspace {workspace_id}',
                    extra={'sid': sid, 'workspace_id': workspace_id}
                )

        @self.socketio.on('join_worktree')
        def handle_join_worktree(data):
            """Subscribe to conflict updates for a worktree."""
            sid = request.sid
            worktree_id = data.get('worktreeId')
            run_id = data.get('runId')
            user_id = self.connected_users.get(sid, 'anonymous')

            if not worktree_id:
                emit('error', {
                    'message': 'worktreeId required',
                    'code': 'INVALID_REQUEST',
                    'timestamp': datetime.utcnow().isoformat(),
                })
                return

            room = f'worktree:{worktree_id}'
            join_room(room)

            if room not in self.subscriptions:
                self.subscriptions[room] = []
            self.subscriptions[room].append(sid)

            logger.info(
                f'User {user_id} joined worktree {worktree_id}',
                extra={'sid': sid, 'worktree_id': worktree_id, 'run_id': run_id}
            )

            emit('worktree_joined', {
                'worktreeId': worktree_id,
                'timestamp': datetime.utcnow().isoformat(),
            })

        @self.socketio.on('leave_worktree')
        def handle_leave_worktree(data):
            """Unsubscribe from conflict updates."""
            sid = request.sid
            worktree_id = data.get('worktreeId')
            user_id = self.connected_users.get(sid, 'anonymous')

            if worktree_id:
                room = f'worktree:{worktree_id}'
                leave_room(room)

                if room in self.subscriptions:
                    self.subscriptions[room] = [
                        s for s in self.subscriptions[room] if s != sid
                    ]

                logger.info(
                    f'User {user_id} left worktree {worktree_id}',
                    extra={'sid': sid, 'worktree_id': worktree_id}
                )

        @self.socketio.on('cancel_run')
        def handle_cancel_run(data):
            """Handle request to cancel a run."""
            sid = request.sid
            run_id = data.get('runId')
            user_id = self.connected_users.get(sid, 'anonymous')

            if not run_id:
                emit('error', {
                    'message': 'runId required',
                    'code': 'INVALID_REQUEST',
                    'timestamp': datetime.utcnow().isoformat(),
                })
                return

            logger.info(
                f'User {user_id} requested cancel for run {run_id}',
                extra={'sid': sid, 'run_id': run_id}
            )

            # Event will be handled by backend service
            # Emit confirmation back to client
            emit('cancel_requested', {
                'runId': run_id,
                'status': 'pending',
                'timestamp': datetime.utcnow().isoformat(),
            })

        @self.socketio.on('resolve_conflicts')
        def handle_resolve_conflicts(data):
            """Handle request to resolve conflicts."""
            sid = request.sid
            worktree_id = data.get('worktreeId')
            strategy = data.get('strategy', 'auto')
            user_id = self.connected_users.get(sid, 'anonymous')

            if not worktree_id:
                emit('error', {
                    'message': 'worktreeId required',
                    'code': 'INVALID_REQUEST',
                    'timestamp': datetime.utcnow().isoformat(),
                })
                return

            logger.info(
                f'User {user_id} requested conflict resolution for {worktree_id}',
                extra={'sid': sid, 'worktree_id': worktree_id, 'strategy': strategy}
            )

            # Event will be handled by backend service
            emit('resolve_requested', {
                'worktreeId': worktree_id,
                'strategy': strategy,
                'status': 'pending',
                'timestamp': datetime.utcnow().isoformat(),
            })

        @self.socketio.on('error')
        def handle_client_error(data):
            """Handle client-side errors."""
            sid = request.sid
            user_id = self.connected_users.get(sid, 'anonymous')
            error_msg = data.get('message', 'Unknown error')

            logger.warning(
                f'Client error from {user_id}: {error_msg}',
                extra={'sid': sid, 'error': error_msg}
            )

    def broadcast_execution_update(
        self,
        workspace_id: str,
        active_runs: list[dict[str, Any]],
        queued_runs: list[dict[str, Any]],
        stats: dict[str, Any]
    ):
        """Broadcast execution status update to workspace subscribers."""
        room = f'workspace:{workspace_id}'

        self.socketio.emit(
            'execution_update',
            {
                'type': 'execution_update',
                'workspaceId': workspace_id,
                'timestamp': datetime.utcnow().isoformat(),
                'data': {
                    'activeRuns': active_runs,
                    'queuedRuns': queued_runs,
                    'stats': stats,
                }
            },
            room=room,
            skip_sid=None
        )

        logger.debug(
            f'Broadcast execution_update to {room}',
            extra={
                'workspace_id': workspace_id,
                'active_count': len(active_runs),
                'queued_count': len(queued_runs),
            }
        )

    def broadcast_conflict_detected(
        self,
        worktree_id: str,
        run_id: str,
        conflicts: list[dict[str, Any]],
        preview: dict[str, Any],
        severity: str
    ):
        """Broadcast conflict detection event."""
        room = f'worktree:{worktree_id}'

        self.socketio.emit(
            'conflict_detected',
            {
                'type': 'conflict_detected',
                'worktreeId': worktree_id,
                'runId': run_id,
                'timestamp': datetime.utcnow().isoformat(),
                'data': {
                    'conflicts': conflicts,
                    'preview': preview,
                    'severity': severity,
                }
            },
            room=room,
            skip_sid=None
        )

        logger.debug(
            f'Broadcast conflict_detected to {room}',
            extra={'worktree_id': worktree_id, 'severity': severity}
        )

    def broadcast_conflict_resolved(self, worktree_id: str, run_id: str):
        """Broadcast conflict resolution event."""
        room = f'worktree:{worktree_id}'

        self.socketio.emit(
            'conflict_resolved',
            {
                'type': 'conflict_resolved',
                'worktreeId': worktree_id,
                'runId': run_id,
                'timestamp': datetime.utcnow().isoformat(),
            },
            room=room,
            skip_sid=None
        )

        logger.debug(
            f'Broadcast conflict_resolved to {room}',
            extra={'worktree_id': worktree_id}
        )

    def broadcast_queue_promoted(
        self,
        workspace_id: str,
        run_id: str,
        slot_number: int
    ):
        """Broadcast queue promotion event."""
        room = f'workspace:{workspace_id}'

        self.socketio.emit(
            'queue_promoted',
            {
                'type': 'queue_promoted',
                'workspaceId': workspace_id,
                'runId': run_id,
                'slotNumber': slot_number,
                'timestamp': datetime.utcnow().isoformat(),
            },
            room=room,
            skip_sid=None
        )

        logger.debug(
            f'Broadcast queue_promoted to {room}',
            extra={'workspace_id': workspace_id, 'run_id': run_id}
        )

    def broadcast_run_completed(
        self,
        workspace_id: str,
        run_id: str,
        status: str
    ):
        """Broadcast run completion event."""
        room = f'workspace:{workspace_id}'

        self.socketio.emit(
            'run_completed',
            {
                'type': 'run_completed',
                'workspaceId': workspace_id,
                'runId': run_id,
                'status': status,
                'timestamp': datetime.utcnow().isoformat(),
            },
            room=room,
            skip_sid=None
        )

        logger.debug(
            f'Broadcast run_completed to {room}',
            extra={'workspace_id': workspace_id, 'run_id': run_id, 'status': status}
        )

    def broadcast_error(
        self,
        target_room: str,
        message: str,
        code: str,
        context: dict[str, Any] | None = None
    ):
        """Broadcast error event to subscribers."""
        self.socketio.emit(
            'error',
            {
                'type': 'error',
                'message': message,
                'code': code,
                'timestamp': datetime.utcnow().isoformat(),
                'context': context or {},
            },
            room=target_room,
            skip_sid=None
        )

        logger.warning(
            f'Broadcast error to {target_room}: {code}',
            extra={'code': code, 'message': message}
        )

    def get_connection_stats(self) -> dict[str, Any]:
        """Get WebSocket connection statistics."""
        return {
            'connected_users': len(self.connected_users),
            'total_subscriptions': len(self.subscriptions),
            'subscribed_rooms': list(self.subscriptions.keys()),
            'timestamp': datetime.utcnow().isoformat(),
        }
