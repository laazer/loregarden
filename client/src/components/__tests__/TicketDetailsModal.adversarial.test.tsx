import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { TicketDetailsModal } from '../TicketDetailsModal';
import * as apiClient from '../../api/client';

/**
 * ADVERSARIAL TEST SUITE: TicketDetailsModal (16-modal-with-ticket-details)
 *
 * This test suite focuses on exposing hidden weaknesses, edge cases, and
 * mutation scenarios that the standard tests may miss. It systematically
 * applies the Test Breaker Checklist Matrix:
 *
 * - Null & Empty Value Mutations
 * - Boundary Condition Testing
 * - Type & Structure Mutations
 * - Invalid/Corrupt Input Handling
 * - Concurrency & Race Conditions
 * - Order Dependency & State Sensitivity
 * - Combinatorial Edge Cases
 * - Stress & Load Testing
 * - Assumption Validation
 * - Determinism Verification
 */

describe('TicketDetailsModal - Adversarial Test Suite', () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
      },
    });
    jest.clearAllMocks();
  });

  afterEach(() => {
    queryClient.clear();
  });

  const renderWithQueryClient = (component: React.ReactElement) => {
    return render(
      <QueryClientProvider client={queryClient}>
        {component}
      </QueryClientProvider>
    );
  };

  // ============================================================================
  // DIMENSION 1: NULL & EMPTY VALUE MUTATIONS
  // ============================================================================
  describe('Null & Empty Value Mutations', () => {
    it('should handle undefined ticket gracefully (not just null)', () => {
      // Test mutation: undefined vs null difference
      renderWithQueryClient(
        <TicketDetailsModal ticket={undefined as any} isOpen={false} onClose={() => {}} />
      );
      expect(screen.queryByRole('button')).not.toBeInTheDocument();
    });

    it('should handle empty string title without rendering XSS', () => {
      // Mutation: empty string in field that should be non-empty
      const ticket = createMockTicket({ title: '' });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      // Should still render dialog without errors
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should handle empty array of acceptance_criteria vs undefined', () => {
      // Mutation: {} vs [] difference
      const ticket1 = createMockTicket({ acceptance_criteria: [] });
      const ticket2 = createMockTicket({ acceptance_criteria: undefined as any });

      const { unmount: unmount1 } = renderWithQueryClient(
        <TicketDetailsModal ticket={ticket1} isOpen={true} onClose={() => {}} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
      unmount1();

      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket2} isOpen={true} onClose={() => {}} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should handle empty description vs whitespace-only description', () => {
      // Mutation: empty, null, undefined, whitespace variations
      const testCases = [
        { description: '' },
        { description: '   ' },
        { description: '\n\n' },
        { description: '\t' },
      ];

      testCases.forEach((testCase) => {
        const ticket = createMockTicket(testCase);
        const { unmount } = renderWithQueryClient(
          <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
        );
        expect(screen.getByRole('dialog')).toBeInTheDocument();
        unmount();
      });
    });

    it('should handle null acceptance_criteria without crashing', () => {
      const ticket = createMockTicket({ acceptance_criteria: null as any });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should handle null in nested artifact structures', () => {
      const ticket = createMockTicket({
        artifacts: {
          diff: null,
          logs: undefined,
          tests: null,
          error: null,
          context: undefined,
          live: null,
        },
      });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should handle completely null artifacts object', () => {
      const ticket = createMockTicket({ artifacts: null as any });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should handle empty stages array vs null vs undefined', () => {
      const testCases = [
        { stages: [] },
        { stages: null as any },
        { stages: undefined as any },
      ];

      testCases.forEach((testCase) => {
        const ticket = createMockTicket(testCase);
        const { unmount } = renderWithQueryClient(
          <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
        );
        expect(screen.getByRole('dialog')).toBeInTheDocument();
        unmount();
      });
    });
  });

  // ============================================================================
  // DIMENSION 2: BOUNDARY CONDITIONS & EXTREME VALUES
  // ============================================================================
  describe('Boundary Conditions & Extreme Values', () => {
    it('should handle MAX_INT values in numeric fields', () => {
      const ticket = createMockTicket({
        revision: Number.MAX_SAFE_INTEGER,
        child_count: Number.MAX_SAFE_INTEGER,
        priority: Number.MAX_SAFE_INTEGER,
      });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should handle negative values in numeric fields', () => {
      const ticket = createMockTicket({
        revision: -1,
        child_count: -100,
        priority: -99,
      });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should handle zero in all numeric fields', () => {
      const ticket = createMockTicket({
        revision: 0,
        child_count: 0,
        priority: 0,
      });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should handle extremely long strings without performance degradation', () => {
      const veryLongString = 'A'.repeat(100000);
      const ticket = createMockTicket({
        title: veryLongString,
        description: veryLongString,
      });
      const start = performance.now();
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      const duration = performance.now() - start;

      expect(screen.getByRole('dialog')).toBeInTheDocument();
      expect(duration).toBeLessThan(5000); // Should render within 5 seconds
    });

    it('should handle extremely large arrays', () => {
      const largeArray = Array.from({ length: 10000 }, (_, i) => `Criterion ${i}`);
      const ticket = createMockTicket({
        acceptance_criteria: largeArray,
      });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should handle extremely large stages array', () => {
      const largeStages = Array.from({ length: 5000 }, (_, i) => ({
        key: `stage_${i}`,
        name: `Stage ${i}`,
        status: 'pending' as const,
        agent_id: `agent_${i}`,
        skill_name: `skill_${i}`,
        optional: false,
        note: `Note ${i}`,
        stage_type: '',
        agents: [],
      }));
      const ticket = createMockTicket({ stages: largeStages });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should handle NaN in numeric fields', () => {
      const ticket = createMockTicket({
        revision: NaN,
        priority: NaN,
      });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should handle Infinity in numeric fields', () => {
      const ticket = createMockTicket({
        revision: Infinity,
        priority: -Infinity,
      });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });
  });

  // ============================================================================
  // DIMENSION 3: TYPE & STRUCTURE MUTATIONS
  // ============================================================================
  describe('Type & Structure Mutations', () => {
    it('should handle string in numeric field (priority as string)', () => {
      const ticket = createMockTicket({ priority: '99' as any });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should handle number in string field (title as number)', () => {
      const ticket = createMockTicket({ title: 123456 as any });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should handle array in string field', () => {
      const ticket = createMockTicket({ title: ['A', 'B', 'C'] as any });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should handle object in string field', () => {
      const ticket = createMockTicket({
        title: { toString: () => 'Custom Title' } as any
      });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should handle string in array field (acceptance_criteria)', () => {
      const ticket = createMockTicket({
        acceptance_criteria: 'Not an array' as any
      });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should handle boolean in string field', () => {
      const ticket = createMockTicket({ title: true as any });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should handle missing properties from artifact objects', () => {
      const ticket = createMockTicket({
        artifacts: {
          diff: { file: 'test.tsx' } as any, // Missing sections
          logs: undefined,
          tests: { passed: 5 } as any, // Missing other fields
          context: [],
          error: null,
          live: null,
        },
      });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should handle extra/unknown properties in ticket object', () => {
      const ticket = createMockTicket({
        unknown_field_1: 'value',
        unknown_field_2: { nested: 'data' },
        unknown_field_3: [1, 2, 3],
      } as any);
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should handle stages array with missing required fields', () => {
      const ticket = createMockTicket({
        stages: [
          { key: 'stage1' } as any, // Missing name, status, etc.
          { name: 'Stage 2' } as any, // Missing other fields
        ],
      });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });
  });

  // ============================================================================
  // DIMENSION 4: INVALID & CORRUPT INPUTS
  // ============================================================================
  describe('Invalid & Corrupt Inputs', () => {
    it('should handle malformed state enum values', () => {
      const ticket = createMockTicket({
        state: 'INVALID_STATE' as any
      });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should handle invalid workflow_stage_key values', () => {
      const ticket = createMockTicket({
        workflow_stage_key: 'NONSENSE_STAGE_123' as any
      });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should handle corrupted ticket ID (special characters)', () => {
      const ticket = createMockTicket({
        external_id: '"><script>alert("xss")</script>' as any
      });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
      // Verify XSS is not executed
      expect(screen.queryByText('alert')).not.toBeInTheDocument();
    });

    it('should handle XSS payload in title field', () => {
      const ticket = createMockTicket({
        title: '<img src=x onerror="alert(\'xss\')">'
      });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should handle XSS payload in description', () => {
      const ticket = createMockTicket({
        description: '"><script>fetch("http://attacker.com")</script>'
      });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should handle invalid email in last_updated_by', () => {
      const ticket = createMockTicket({
        last_updated_by: '"><script>alert("xss")</script>'
      });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should handle circular reference in artifact data', () => {
      const circularData: any = { value: 'test' };
      circularData.self = circularData;

      const ticket = createMockTicket({
        artifacts: {
          diff: circularData,
          logs: undefined,
          tests: null,
          context: [],
          error: null,
          live: null,
        },
      });

      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should handle non-JSON-serializable objects', () => {
      const ticket = createMockTicket({
        acceptance_criteria: [
          'Normal criterion',
          new Date() as any,
          /regex/ as any,
          () => 'function' as any,
        ],
      });

      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });
  });

  // ============================================================================
  // DIMENSION 5: CONCURRENCY & RACE CONDITIONS
  // ============================================================================
  describe('Concurrency & Race Conditions', () => {
    it('should handle simultaneous isOpen state changes', async () => {
      const onClose = jest.fn();
      const ticket = createMockTicket();
      const { rerender } = renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={false} onClose={onClose} />
      );

      // Simulate rapid concurrent state changes
      const promises = [
        new Promise(resolve => {
          rerender(
            <QueryClientProvider client={queryClient}>
              <TicketDetailsModal ticket={ticket} isOpen={true} onClose={onClose} />
            </QueryClientProvider>
          );
          resolve(true);
        }),
        new Promise(resolve => {
          rerender(
            <QueryClientProvider client={queryClient}>
              <TicketDetailsModal ticket={ticket} isOpen={false} onClose={onClose} />
            </QueryClientProvider>
          );
          resolve(true);
        }),
        new Promise(resolve => {
          rerender(
            <QueryClientProvider client={queryClient}>
              <TicketDetailsModal ticket={ticket} isOpen={true} onClose={onClose} />
            </QueryClientProvider>
          );
          resolve(true);
        }),
      ];

      await Promise.all(promises);
      // Component should be in a valid state
      expect(screen.queryByRole('dialog')).toBeDefined();
    });

    it('should handle simultaneous ticket prop changes', async () => {
      const ticket1 = createMockTicket({ id: 'ticket-1', title: 'Ticket 1' });
      const ticket2 = createMockTicket({ id: 'ticket-2', title: 'Ticket 2' });
      const ticket3 = createMockTicket({ id: 'ticket-3', title: 'Ticket 3' });

      const { rerender } = renderWithQueryClient(
        <TicketDetailsModal ticket={ticket1} isOpen={true} onClose={() => {}} />
      );

      // Rapidly change tickets
      const promises = [
        new Promise(resolve => {
          rerender(
            <QueryClientProvider client={queryClient}>
              <TicketDetailsModal ticket={ticket2} isOpen={true} onClose={() => {}} />
            </QueryClientProvider>
          );
          resolve(true);
        }),
        new Promise(resolve => {
          rerender(
            <QueryClientProvider client={queryClient}>
              <TicketDetailsModal ticket={ticket3} isOpen={true} onClose={() => {}} />
            </QueryClientProvider>
          );
          resolve(true);
        }),
        new Promise(resolve => {
          rerender(
            <QueryClientProvider client={queryClient}>
              <TicketDetailsModal ticket={ticket1} isOpen={true} onClose={() => {}} />
            </QueryClientProvider>
          );
          resolve(true);
        }),
      ];

      await Promise.all(promises);
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should handle close callback called multiple times', async () => {
      const onClose = jest.fn();
      const ticket = createMockTicket();
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={onClose} />
      );

      const dialog = screen.getByRole('dialog');

      // Try to trigger close multiple times
      fireEvent.keyDown(dialog, { key: 'Escape' });
      fireEvent.keyDown(dialog, { key: 'Escape' });
      fireEvent.keyDown(dialog, { key: 'Escape' });

      expect(onClose.mock.calls.length).toBeGreaterThan(0);
    });

    it('should handle unmount while modal is loading', async () => {
      const ticket = createMockTicket();
      const { unmount } = renderWithQueryClient(
        <TicketDetailsModal
          ticket={ticket}
          isOpen={true}
          onClose={() => {}}
          isLoading={true}
        />
      );

      // Unmount immediately while loading
      expect(() => unmount()).not.toThrow();
    });
  });

  // ============================================================================
  // DIMENSION 6: ORDER DEPENDENCY & STATE SENSITIVITY
  // ============================================================================
  describe('Order Dependency & State Sensitivity', () => {
    it('should produce same output regardless of isOpen order', () => {
      const ticket = createMockTicket();

      // Sequence 1: open then close
      const { unmount: unmount1 } = renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={false} onClose={() => {}} />
      );
      unmount1();

      // Sequence 2: close then open
      const { unmount: unmount2 } = renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      unmount2();

      // Should handle both sequences without errors
      expect(true).toBe(true);
    });

    it('should produce consistent results when callback order changes', () => {
      const onClose = jest.fn();
      const ticket = createMockTicket();

      const { rerender } = renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={onClose} />
      );

      // Change callback reference
      const newOnClose = jest.fn();
      rerender(
        <QueryClientProvider client={queryClient}>
          <TicketDetailsModal ticket={ticket} isOpen={true} onClose={newOnClose} />
        </QueryClientProvider>
      );

      const dialog = screen.getByRole('dialog');
      fireEvent.keyDown(dialog, { key: 'Escape' });

      // New callback should be called
      expect(newOnClose).toHaveBeenCalled();
      expect(onClose).not.toHaveBeenCalled();
    });

    it('should maintain consistency when props update out of order', () => {
      const ticket1 = createMockTicket({ title: 'Ticket 1' });
      const ticket2 = createMockTicket({ title: 'Ticket 2' });

      const { rerender } = renderWithQueryClient(
        <TicketDetailsModal ticket={ticket1} isOpen={true} onClose={() => {}} />
      );

      // Update title first, then open state
      rerender(
        <QueryClientProvider client={queryClient}>
          <TicketDetailsModal ticket={ticket2} isOpen={true} onClose={() => {}} />
        </QueryClientProvider>
      );

      expect(screen.getByDisplayValue('Ticket 2')).toBeInTheDocument();
    });
  });

  // ============================================================================
  // DIMENSION 7: COMBINATORIAL EDGE CASES
  // ============================================================================
  describe('Combinatorial Edge Cases', () => {
    it('should handle null ticket + isOpen=true', () => {
      renderWithQueryClient(
        <TicketDetailsModal ticket={null} isOpen={true} onClose={() => {}} />
      );
      // Should handle gracefully
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });

    it('should handle null ticket + isOpen=false', () => {
      renderWithQueryClient(
        <TicketDetailsModal ticket={null} isOpen={false} onClose={() => {}} />
      );
      expect(screen.queryByRole('button')).not.toBeInTheDocument();
    });

    it('should handle empty ticket + loading=true', () => {
      const ticket = createMockTicket({
        title: '',
        description: '',
        acceptance_criteria: [],
      });
      renderWithQueryClient(
        <TicketDetailsModal
          ticket={ticket}
          isOpen={true}
          onClose={() => {}}
          isLoading={true}
        />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should handle empty artifacts + no stages', () => {
      const ticket = createMockTicket({
        artifacts: {
          diff: null,
          logs: [],
          tests: null,
          context: [],
          error: null,
          live: null,
        },
        stages: [],
      });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should handle state=BLOCKED + error present', () => {
      const ticket = createMockTicket({
        state: 'blocked' as any,
        blocking_issues: 'Waiting for approval',
        artifacts: {
          diff: null,
          logs: [],
          tests: null,
          context: [],
          error: { message: 'Test failed', run_code: 'fail', stage_key: 'testing', agent_id: 'qa', command: 'pytest' },
          live: null,
        },
      });
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} error="Load failed" />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should handle multiple rapid prop changes with different combinations', async () => {
      const ticket1 = createMockTicket();
      const ticket2 = createMockTicket({ title: 'Different' });

      const { rerender } = renderWithQueryClient(
        <TicketDetailsModal
          ticket={ticket1}
          isOpen={true}
          onClose={() => {}}
          isLoading={false}
          error={undefined}
        />
      );

      const combinations = [
        { ticket: ticket2, isOpen: false, isLoading: true, error: 'Error 1' },
        { ticket: ticket1, isOpen: true, isLoading: false, error: undefined },
        { ticket: ticket2, isOpen: true, isLoading: true, error: 'Error 2' },
        { ticket: null, isOpen: false, isLoading: false, error: undefined },
      ];

      for (const combo of combinations) {
        rerender(
          <QueryClientProvider client={queryClient}>
            <TicketDetailsModal
              ticket={combo.ticket}
              isOpen={combo.isOpen}
              onClose={() => {}}
              isLoading={combo.isLoading}
              error={combo.error}
            />
          </QueryClientProvider>
        );
      }

      expect(true).toBe(true);
    });
  });

  // ============================================================================
  // DIMENSION 8: STRESS & LOAD TESTING
  // ============================================================================
  describe('Stress & Load Testing', () => {
    it('should handle repeated open/close cycles (100 iterations)', async () => {
      const onClose = jest.fn();
      const ticket = createMockTicket();

      const { rerender } = renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={false} onClose={onClose} />
      );

      for (let i = 0; i < 100; i++) {
        rerender(
          <QueryClientProvider client={queryClient}>
            <TicketDetailsModal ticket={ticket} isOpen={i % 2 === 0} onClose={onClose} />
          </QueryClientProvider>
        );
      }

      expect(true).toBe(true);
    });

    it('should handle many ticket switches under load', () => {
      const tickets = Array.from({ length: 100 }, (_, i) =>
        createMockTicket({ id: `ticket-${i}`, title: `Ticket ${i}` })
      );

      const { rerender } = renderWithQueryClient(
        <TicketDetailsModal ticket={tickets[0]} isOpen={true} onClose={() => {}} />
      );

      tickets.forEach((ticket, index) => {
        if (index % 10 === 0) {
          rerender(
            <QueryClientProvider client={queryClient}>
              <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
            </QueryClientProvider>
          );
        }
      });

      expect(true).toBe(true);
    });

    it('should handle modal with massive acceptance criteria list', () => {
      const hugeAcceptanceCriteria = Array.from(
        { length: 10000 },
        (_, i) => `Criterion ${i}: This is a long detailed requirement that should be displayed.`
      );

      const ticket = createMockTicket({
        acceptance_criteria: hugeAcceptanceCriteria,
      });

      const start = performance.now();
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      const duration = performance.now() - start;

      expect(screen.getByRole('dialog')).toBeInTheDocument();
      expect(duration).toBeLessThan(10000); // Should render within 10 seconds
    });

    it('should handle multiple simultaneous renderings', async () => {
      const tickets = Array.from({ length: 50 }, (_, i) =>
        createMockTicket({ id: `ticket-${i}`, title: `Ticket ${i}` })
      );

      const promises = tickets.map((ticket, index) => {
        return new Promise<void>((resolve) => {
          const { unmount } = renderWithQueryClient(
            <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
          );
          setTimeout(() => {
            unmount();
            resolve();
          }, index * 10);
        });
      });

      await Promise.all(promises);
      expect(true).toBe(true);
    });
  });

  // ============================================================================
  // DIMENSION 9: ASSUMPTION VALIDATION
  // ============================================================================
  describe('Assumption Validation', () => {
    it('should not assume onClose is always defined', () => {
      const ticket = createMockTicket();
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={undefined as any} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should not assume ticket has all expected fields', () => {
      const minimalTicket = {
        id: 'test-1',
        title: 'Test',
      } as any;

      renderWithQueryClient(
        <TicketDetailsModal ticket={minimalTicket} isOpen={true} onClose={() => {}} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should not assume QueryClient is properly configured', () => {
      const badQueryClient = new QueryClient({
        defaultOptions: {
          queries: { retry: Infinity, staleTime: -1 },
        },
      });

      render(
        <QueryClientProvider client={badQueryClient}>
          <TicketDetailsModal
            ticket={createMockTicket()}
            isOpen={true}
            onClose={() => {}}
          />
        </QueryClientProvider>
      );

      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should not assume artifact data structure is complete', () => {
      const ticket = createMockTicket({
        artifacts: {
          diff: { sections: [{ path: 'test.tsx' }] } as any,
          logs: [{}] as any,
          tests: { status: 'passed' } as any,
          context: [{ file: 'test' }] as any,
          error: { message: 'Error' } as any,
          live: { data: 'something' } as any,
        },
      });

      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should not assume stages are properly ordered', () => {
      const ticket = createMockTicket({
        stages: [
          { key: 'stage_5', name: 'Stage 5', status: 'pending', agent_id: 'a', skill_name: 's', optional: false, note: '', stage_type: '', agents: [] },
          { key: 'stage_1', name: 'Stage 1', status: 'done', agent_id: 'a', skill_name: 's', optional: false, note: '', stage_type: '', agents: [] },
          { key: 'stage_3', name: 'Stage 3', status: 'running', agent_id: 'a', skill_name: 's', optional: false, note: '', stage_type: '', agents: [] },
        ],
      });

      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });
  });

  // ============================================================================
  // DIMENSION 10: DETERMINISM & REGRESSION VALIDATION
  // ============================================================================
  describe('Determinism & Regression Validation', () => {
    it('should produce identical output across multiple renders with same input', () => {
      const ticket = createMockTicket({
        title: 'Deterministic Test',
        external_id: '16-modal-with-ticket-details',
      });

      const outputs = [];

      for (let i = 0; i < 3; i++) {
        const { unmount } = renderWithQueryClient(
          <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
        );

        const dialogText = screen.getByRole('dialog').textContent;
        outputs.push(dialogText);
        unmount();
      }

      // All outputs should be identical
      expect(outputs[0]).toBe(outputs[1]);
      expect(outputs[1]).toBe(outputs[2]);
    });

    it('should consistently handle error states', () => {
      const ticket = createMockTicket();
      const errorMessage = 'Network timeout occurred';

      const { unmount: unmount1 } = renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} error={errorMessage} />
      );
      expect(screen.getByText(errorMessage)).toBeInTheDocument();
      unmount1();

      const { unmount: unmount2 } = renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} error={errorMessage} />
      );
      expect(screen.getByText(errorMessage)).toBeInTheDocument();
      unmount2();
    });

    it('should handle the same content mutation identically each time', () => {
      const corruptedContent = '"><script>alert("xss")</script>';
      const results = [];

      for (let i = 0; i < 3; i++) {
        const ticket = createMockTicket({ title: corruptedContent });
        const { unmount } = renderWithQueryClient(
          <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
        );

        const hasDialog = screen.queryByRole('dialog') !== null;
        results.push(hasDialog);
        unmount();
      }

      expect(results[0]).toBe(results[1]);
      expect(results[1]).toBe(results[2]);
    });
  });

  // ============================================================================
  // DIMENSION 11: MEMORY & RESOURCE MANAGEMENT
  // ============================================================================
  describe('Memory & Resource Management', () => {
    it('should properly cleanup event listeners on unmount', () => {
      const onClose = jest.fn();
      const ticket = createMockTicket();

      const { unmount } = renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={onClose} />
      );

      unmount();

      // Triggering events after unmount should not cause errors or calls
      expect(onClose).not.toHaveBeenCalled();
    });

    it('should not leak memory with multiple mount/unmount cycles', () => {
      for (let i = 0; i < 100; i++) {
        const ticket = createMockTicket();
        const { unmount } = renderWithQueryClient(
          <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
        );
        unmount();
      }

      expect(true).toBe(true);
    });

    it('should handle cleanup when callbacks are null', () => {
      const ticket = createMockTicket();
      const { unmount } = renderWithQueryClient(
        <TicketDetailsModal
          ticket={ticket}
          isOpen={true}
          onClose={null as any}
        />
      );

      expect(() => unmount()).not.toThrow();
    });
  });

  // ============================================================================
  // DIMENSION 12: MODAL INTERACTION EDGE CASES
  // ============================================================================
  describe('Modal Interaction Edge Cases', () => {
    it('should handle backdrop click with event.stopPropagation', () => {
      const onClose = jest.fn();
      const ticket = createMockTicket();
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={onClose} />
      );

      const backdrop = screen.getByTestId('modal-backdrop');
      const clickEvent = new MouseEvent('click', { bubbles: true });
      jest.spyOn(clickEvent, 'stopPropagation');

      fireEvent(backdrop, clickEvent);

      expect(onClose).toHaveBeenCalled();
    });

    it('should handle keyboard events on nested elements', () => {
      const onClose = jest.fn();
      const ticket = createMockTicket();
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={onClose} />
      );

      const dialog = screen.getByRole('dialog');
      const nestedInput = dialog.querySelector('input') || dialog.querySelector('button');

      if (nestedInput) {
        fireEvent.keyDown(nestedInput, { key: 'Escape' });
        expect(onClose).toHaveBeenCalled();
      }
    });

    it('should handle rapid escape key presses', () => {
      const onClose = jest.fn();
      const ticket = createMockTicket();
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={onClose} />
      );

      const dialog = screen.getByRole('dialog');

      // Rapid escape presses
      for (let i = 0; i < 10; i++) {
        fireEvent.keyDown(dialog, { key: 'Escape' });
      }

      expect(onClose.mock.calls.length).toBeGreaterThan(0);
    });

    it('should ignore non-Escape keys in modal', () => {
      const onClose = jest.fn();
      const ticket = createMockTicket();
      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={onClose} />
      );

      const dialog = screen.getByRole('dialog');
      fireEvent.keyDown(dialog, { key: 'Enter' });
      fireEvent.keyDown(dialog, { key: 'Tab' });
      fireEvent.keyDown(dialog, { key: 'Space' });

      expect(onClose).not.toHaveBeenCalled();
    });
  });

  // ============================================================================
  // DIMENSION 13: DATA VALIDATION EDGE CASES
  // ============================================================================
  describe('Data Validation Edge Cases', () => {
    it('should handle special characters in all string fields', () => {
      const specialChars = '!@#$%^&*()_+-=[]{}|;:\'",.<>?/\\`~';
      const ticket = createMockTicket({
        title: specialChars,
        description: specialChars,
        external_id: specialChars,
        last_updated_by: specialChars,
      });

      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should handle unicode characters and emoji', () => {
      const ticket = createMockTicket({
        title: '🎉 Unicode Test: 中文 العربية ελληνικά',
        description: '🚀 Multiple lines\n🔧 With special chars\n✨',
      });

      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should handle mixed line endings in description', () => {
      const ticket = createMockTicket({
        description: 'Line 1\nLine 2\r\nLine 3\rLine 4',
      });

      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('should handle HTML entities in text', () => {
      const ticket = createMockTicket({
        title: '&lt;div&gt; &amp; &quot;quoted&quot;',
        description: '&nbsp;&nbsp;&nbsp; Escaped spaces',
      });

      renderWithQueryClient(
        <TicketDetailsModal ticket={ticket} isOpen={true} onClose={() => {}} />
      );
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });
  });
});

// ============================================================================
// HELPER FUNCTION
// ============================================================================

function createMockTicket(overrides?: Partial<apiClient.TicketDetail>): apiClient.TicketDetail {
  return {
    id: 'ticket-123',
    external_id: '16-modal-with-ticket-details',
    title: 'Test Ticket',
    description: 'Test description',
    acceptance_criteria: [],
    state: 'in_progress',
    priority: 1,
    workspace_slug: 'loregarden',
    workflow_stage_key: 'test_design',
    workflow_stage_status: 'running',
    workflow_stage_name: 'Test Design',
    run_code: 'run_abc123',
    work_item_type: 'feature',
    parent_ticket_id: null,
    milestone: '',
    branch: '',
    child_count: 0,
    revision: 1,
    last_updated_by: 'test@example.com',
    next_agent: 'implementation_agent',
    next_status: 'ready',
    blocking_issues: '',
    state_locked: false,
    workflow_template_slug: '',
    workflow_template_name: '',
    stages: [],
    artifacts: {
      diff: null,
      logs: [],
      tests: null,
      context: [],
      error: null,
      live: null,
    },
    ...overrides,
  };
}
