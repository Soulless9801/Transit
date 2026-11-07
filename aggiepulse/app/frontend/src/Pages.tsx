import Forecast from './tabs/Forecast';
import Events from './tabs/Events';
import PlanTrip from './tabs/PlanTrip';
import History from './tabs/History';
export default function Pages({page}: {page: string}) {
  switch (page) {
    case 'Forecast': return <Forecast/>;
    case 'Events': return <Events/>;
    case 'Plan my trip': return <PlanTrip/>;
    case 'History': return <History/>;
    default: return null;
  }
}
