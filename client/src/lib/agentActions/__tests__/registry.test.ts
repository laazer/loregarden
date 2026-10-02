import { UiActionRegistry } from '../registry';

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
