export { ApiError, apiClient, apiRequest } from "./client";
export type {
  ApiCredentials,
  ApplicationListItemDto,
  ApplicationReportResponseDto,
  ApplicationsAnalyticsResponseDto,
  CurrentUserDto,
  DepartmentsAnalyticsResponseDto,
  ExecutorsAnalyticsResponseDto,
  NotificationDto,
  WorkTypesAnalyticsResponseDto,
} from "./client";
export { mapAdUser, mapApplication, mapCurrentUser, mapNotification, mapUser } from "./mappers";
