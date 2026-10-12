import { useMemo } from "react";
import { adaptSessionData } from "../services/insightAdapter";

export default function useFunnelAnalysis(session, scriptSegments, danmakus) {
  return useMemo(() => {
    if (!session) return null;
    return adaptSessionData(session, scriptSegments, danmakus);
  }, [
    session?.id,
    session?.status,
    session?.composite_score,
    session?.analysis_summary,
    scriptSegments.length,
    danmakus.length,
  ]);
}
