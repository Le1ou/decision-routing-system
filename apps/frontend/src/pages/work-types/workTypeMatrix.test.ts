import { describe, expect, it } from "vitest";

import type { Grade, Position, WorkType } from "@shared/model/domain";
import {
  createDefaultMatrix,
  getMatrixSelectionCount,
  getPositionGradeSummary,
  hydrateWorkTypeMatrix,
  toWorkTypeAccessPayload,
  toggleMatrixGrade,
} from "./workTypeMatrix";

const grades: Grade[] = [
  { id: "junior", name: "Младший" },
  { id: "middle", name: "Старший" },
  { id: "senior", name: "Ведущий" },
];

const positions: Position[] = [
  { id: "engineer", name: "Инженер", gradeIds: ["junior", "middle"] },
  { id: "lead-engineer", name: "Ведущий инженер", gradeIds: ["senior"] },
  { id: "ungraded", name: "Без матрицы", gradeIds: [] },
];

const workType = (overrides: Partial<WorkType> = {}): WorkType => ({
  id: "wt1",
  name: "Настройка",
  departmentId: "it",
  complexity: "medium",
  allowedGradeIds: ["junior", "senior"],
  allowedPositionIds: ["engineer", "lead-engineer"],
  ...overrides,
});

describe("workTypeMatrix", () => {
  it("creates a default matrix from position grades", () => {
    expect(createDefaultMatrix(positions, grades)).toEqual({
      engineer: ["junior", "middle"],
      "lead-engineer": ["senior"],
      ungraded: ["junior", "middle", "senior"],
    });
  });

  it("hydrates from future backend positionGradeMatrix when present", () => {
    expect(hydrateWorkTypeMatrix(workType({
      positionGradeMatrix: {
        engineer: ["middle"],
        "lead-engineer": ["senior"],
      },
    }), positions)).toEqual({
      engineer: ["middle"],
      "lead-engineer": ["senior"],
      ungraded: [],
    });
  });

  it("hydrates from legacy allowed grades and positions", () => {
    expect(hydrateWorkTypeMatrix(workType(), positions)).toEqual({
      engineer: ["junior"],
      "lead-engineer": ["senior"],
      ungraded: [],
    });
  });

  it("treats empty legacy allowed positions as every position restricted only by grades", () => {
    expect(hydrateWorkTypeMatrix(workType({ allowedPositionIds: [] }), positions)).toEqual({
      engineer: ["junior"],
      "lead-engineer": ["senior"],
      ungraded: [],
    });
  });

  it("toggles one grade without mutating the original matrix", () => {
    const matrix = { engineer: ["junior"] };
    const nextMatrix = toggleMatrixGrade(matrix, "engineer", "middle");

    expect(nextMatrix).toEqual({ engineer: ["junior", "middle"] });
    expect(matrix).toEqual({ engineer: ["junior"] });
    expect(toggleMatrixGrade(nextMatrix, "engineer", "junior")).toEqual({ engineer: ["middle"] });
  });

  it("converts matrix to current and future API payload fields", () => {
    expect(toWorkTypeAccessPayload({
      engineer: ["junior", "middle"],
      "lead-engineer": ["middle", "senior"],
      ungraded: [],
    })).toEqual({
      allowedGradeIds: ["junior", "middle", "senior"],
      allowedPositionIds: ["engineer", "lead-engineer"],
      positionGradeMatrix: {
        engineer: ["junior", "middle"],
        "lead-engineer": ["middle", "senior"],
      },
    });
  });

  it("counts and summarizes selected cells", () => {
    const matrix = {
      engineer: ["junior", "middle"],
      "lead-engineer": [],
    };

    expect(getMatrixSelectionCount(matrix)).toBe(2);
    expect(getPositionGradeSummary(matrix, positions, grades)).toBe("Инженер: Младший, Старший");
    expect(getPositionGradeSummary({}, positions, grades)).toBe("Нет выбранных должностей и позиций");
  });
});
