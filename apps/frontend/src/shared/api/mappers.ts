import type { CurrentUserDto } from "./client";
import type { User } from "@shared/model/domain";
import { getPrimaryRole, normalizeRoles } from "@shared/model/roles";

export function mapCurrentUser(dto: CurrentUserDto): User {
  const roles = normalizeRoles(dto.user.roles);
  const role = getPrimaryRole(roles);

  return {
    id: dto.user.id,
    login: dto.user.login,
    fullName: dto.user.fullName,
    roles,
    role,
    departmentId: dto.user.departmentId,
    postName: dto.user.postName,
    positionId: dto.user.positionId,
    jobTitleId: dto.user.positionId,
    isActive: dto.user.isActive,
  };
}
