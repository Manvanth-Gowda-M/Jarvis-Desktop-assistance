# Java Inheritance Summary
- **Date:** 2026-10-29 12:46
- **Tags:** Java, OOP, Inheritance, Programming, Tutorial

## Summary & Analysis
# Java Inheritance Summary

## Definition
Inheritance is a fundamental OOP concept in Java where a class (subclass/child) can inherit properties and methods from another class (superclass/parent). It enables code reuse and establishes an "is-a" relationship.

## Syntax
```java
class SuperClass {
    // Fields and methods
}

class SubClass extends SuperClass {
    // Additional fields and methods
}
```

## Types of Inheritance

### 1. Single Inheritance
One subclass inherits from one superclass.
```java
class Animal {
    void speak() { System.out.println("Animal sound"); }
}
class Dog extends Animal {
    void bark() { System.out.println("Woof!"); }
}
```

### 2. Multilevel Inheritance
A subclass inherits from another subclass.
```java
class Animal { void eat(); }
class Dog extends Animal { void bark(); }
class Puppy extends Dog { void whine(); }
```

### 3. Hierarchical Inheritance
Multiple subclasses inherit from the same superclass.
```java
class Animal { void breathe(); }
class Dog extends Animal { }
class Cat extends Animal { }
class Cow extends Animal { }
```

### 4. Multiple Inheritance (Not directly supported)
Java doesn't support multiple class inheritance but achieves it through interfaces.
```java
interface Flyable { void fly(); }
interface Swimmable { void swim(); }
class Duck implements Flyable, Swimmable { }
```

## Key Concepts

### The `super` Keyword
- Refers to the immediate parent class
- Can call parent constructor: `super();` or `super(parameters);`
- Can access parent methods: `super.methodName();`

### Method Overriding
Same method name in subclass with same signature but different implementation.
```java
class Animal {
    void makeSound() { System.out.println("Animal sound"); }
}
class Dog extends Animal {
    @Override
    void makeSound() { System.out.println("Woof!"); }
}
```

### Constructor Chaining
Constructors are called from subclass to superclass automatically.
```java
class Animal {
    Animal() { System.out.println("Animal constructor"); }
}
class Dog extends Animal {
    Dog() { System.out.println("Dog constructor"); }
}
// Output: Animal constructor, then Dog constructor
```

### Access Modifiers and Inheritance
- `public`: accessible everywhere
- `protected`: accessible within package and subclasses
- `private`: not accessible to subclasses
- Default (package-private): accessible within same package

## Best Practices
1. Use inheritance for "is-a" relationships
2. Keep superclasses general and abstract when appropriate
3. Use `@Override` annotation for overridden methods
4. Avoid deep inheritance hierarchies (prefer composition)

## Common Pitfalls
- Trying to inherit from multiple classes (use interfaces instead)
- Hiding static methods (cannot override static methods)
- Forgetting to call `super()` in subclass constructors
- Creating circular inheritance relationships

## Sources
Java Language Specification, Oracle Java Tutorials, OOP principles
