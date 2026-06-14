import { describe, expect, it } from "vitest";

import type { ApplicationDetailDto, CurrentUserDto } from "./client";
import { mapApplication, mapCurrentUser, mapUser } from "./mappers";

describe("api mappers", () => {
  it("maps current user with normalized primary role", () => {
    const dto: CurrentUserDto = {
      user: {
        id: "1",
        login: "ivan",
        fullName: "Иван Иванов",
        roles: ["author", "manager"],
        departmentId: "it",
        postName: "Руководитель",
        positionId: "manager",
        isActive: true,
      },
      permissions: {
        canManageEmployees: true,
        canManageWorkTypes: true,
        canManagePrioritySettings: false,
        canViewReports: true,
      },
    };

    expect(mapCurrentUser(dto)).toMatchObject({
      id: "1",
      role: "manager",
      roles: ["author", "manager"],
      jobTitleId: "manager",
    });
  });

  it("maps backend employee aliases", () => {
    expect(mapUser({
      employee_id: 7,
      fio: "Мария Петрова",
      role: "executor",
      department_id: 3,
      post_id: 5,
      post_name: "Инженер",
      is_active: false,
    })).toMatchObject({
      id: "7",
      fullName: "Мария Петрова",
      role: "executor",
      departmentId: "3",
      positionId: "5",
      isActive: false,
    });
  });

  it("maps application detail, nested records, attachments, and work type matrix", () => {
    const dto: ApplicationDetailDto = {
      id: "A-1",
      name: "Починить терминал",
      status: "assigned",
      priority: "high",
      createdAt: "2026-06-01T00:00:00.000Z",
      description: "Не включается",
      departmentId: "it",
      workTypeId: "repair",
      authorId: "author-1",
      executorId: "executor-1",
      previousExecutorId: null,
      isUnfinished: true,
      deadlineAt: "2026-06-02T00:00:00.000Z",
      updatedAt: "2026-06-01T01:00:00.000Z",
      assignedComplexity: "hard",
      assignedAt: "2026-06-01T02:00:00.000Z",
      attachments: [
        { photo_id: 10, name: "photo.png", content_type: "image/png", url: "https://example.test/photo.png" },
        { attachment_id: 11, fileName: "manual.pdf", contentType: "application/pdf" },
      ],
      workType: {
        type_of_works_id: 1,
        name: "Ремонт",
        department_id: 2,
        complexity: "hard",
        allowedGradeIds: [1, "2"],
        allowedPositionIds: ["engineer"],
        positionGradeMatrix: {
          engineer: ["1", 2],
        },
      },
      author: {
        employee_id: "author-1",
        fio: "Автор",
        role: "author",
        department_id: "it",
        post_id: "author-post",
      },
      executor: {
        employee_id: "executor-1",
        fio: "Исполнитель",
        role: "executor",
        department_id: "it",
        post_id: "engineer",
      },
    };

    expect(mapApplication(dto)).toMatchObject({
      id: "A-1",
      title: "Починить терминал",
      status: "assigned",
      priority: "high",
      assignedComplexity: "hard",
      attachmentNames: ["photo.png", "manual.pdf"],
      attachments: [
        { id: "10", type: "photo" },
        { id: "11", type: "document" },
      ],
      workType: {
        id: "1",
        departmentId: "2",
        allowedGradeIds: ["1", "2"],
        allowedPositionIds: ["engineer"],
        positionGradeMatrix: {
          engineer: ["1", "2"],
        },
      },
      author: { fullName: "Автор" },
      executor: { fullName: "Исполнитель" },
    });
  });
});
