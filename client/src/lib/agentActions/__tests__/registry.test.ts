import { NotThisTarget, UiActionRegistry } from '../registry';

it('the latest registration wins, and unmounting it restores the one before', async () => {
  const registry = new UiActionRegistry();
  const outer = registry.register('ticket.open', async () => 'outer');
  const inner = registry.register('ticket.open', async () => 'inner');
  await expect(registry.run('ticket.open', { ticket_id: 'x' })).resolves.toBe('inner');

  inner();
  await expect(registry.run('ticket.open', { ticket_id: 'x' })).resolves.toBe('outer');

  outer();
  expect(registry.available()).toEqual([]);
});

it('refuses an action nothing offers, by name', async () => {
  const registry = new UiActionRegistry();
  await expect(registry.run('ticket.update', { ticket_id: 'x' })).rejects.toThrow(
    'this tab no longer offers ticket.update',
  );
});

it('tells listeners about every change to what is available', () => {
  const registry = new UiActionRegistry();
  const listener = jest.fn();
  registry.subscribe(listener);
  const off = registry.register('navigate.page', async () => null);
  off();
  off(); // a second unregister is a no-op: nothing changed, so nothing to report
  expect(listener).toHaveBeenCalledTimes(2);
  expect(registry.available()).toEqual([]);
});

it('asks each instance in turn when one says the target is not its own', async () => {
  const registry = new UiActionRegistry();
  registry.register('run.cancel', async ({ run_id }) => {
    if (run_id !== 'r1') throw new NotThisTarget('this composer is for r1');
    return 'cancelled r1';
  });
  registry.register('run.cancel', async ({ run_id }) => {
    if (run_id !== 'r2') throw new NotThisTarget('this composer is for r2');
    return 'cancelled r2';
  });
  await expect(registry.run('run.cancel', { run_id: 'r1' })).resolves.toBe('cancelled r1');
  await expect(registry.run('run.cancel', { run_id: 'r2' })).resolves.toBe('cancelled r2');
  await expect(registry.run('run.cancel', { run_id: 'r3' })).rejects.toThrow('this composer is for r1');
});

it('a real refusal stops the search instead of falling through', async () => {
  const registry = new UiActionRegistry();
  const older = jest.fn(async () => 'should not run');
  registry.register('run.cancel', older);
  registry.register('run.cancel', async () => {
    throw new Error('the run already finished');
  });
  await expect(registry.run('run.cancel', { run_id: 'r1' })).rejects.toThrow('the run already finished');
  expect(older).not.toHaveBeenCalled();
});
