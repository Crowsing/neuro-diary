import { groupBadges } from '../../lib/groups';
import type { AppData } from '../../lib/types';

export default function GroupBadges({ symptomId, data }: { symptomId: string; data: AppData }) {
  const badges = groupBadges(symptomId, data);
  if (!badges.length) return <span className="tag tag-neutral nd-group-badge">Без групи</span>;
  return (
    <span className="nd-group-badges" aria-label={'Групи: ' + badges.join(', ')}>
      {badges.map((name) => <span key={name} className="tag tag-outline nd-group-badge">{name}</span>)}
    </span>
  );
}
