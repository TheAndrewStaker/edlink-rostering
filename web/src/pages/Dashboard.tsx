import { AlertsBanner } from "@/components/AlertsBanner";
import { KpiStrip } from "@/components/KpiStrip";
import { SyncActivityRail } from "@/components/SyncActivityRail";

export function DashboardPage() {
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Dashboard</h1>
        </div>
        <div className="actions" />
      </div>

      <KpiStrip />

      <div className="dash-row">
        <AlertsBanner />
        <SyncActivityRail />
      </div>
    </>
  );
}
