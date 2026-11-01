# Java Inheritance - Complete Overview

## What is Inheritance?

**Inheritance** is a fundamental Object-Oriented Programming (OOP) concept where a class (called the **subclass** or **child class**) can inherit properties and methods from another class (called the **superclass** or **parent class**).

### Purpose of Inheritance:
- **Code Reusability**: Avoid rewriting similar code
- **Establishing "Is-A" Relationships**: A Dog "is an" Animal
- **Method Overriding**: Provide specific implementations of parent class methods
- **Polymorphism Support**: Enables objects to take multiple forms

---

## Java Inheritance Syntax

```java
// Simple inheritance
class ParentClass {
    // fields and methods
}

class ChildClass extends ParentClass {
    // inherits from ParentClass, can add own fields/methods
}
```

The `extends` keyword is used to inherit from a class.

**Limitation**: Java does not support multiple inheritance with classes (a class cannot extend more than one parent class).

---

## Types of Inheritance in Java

### 1. Single Inheritance
```java
class Animal { }
class Dog extends Animal { }  // Single level
```

### 2. Hierarchical Inheritance
```java
class Animal { }
class Dog extends Animal { }
class Cat extends Animal { }  // Multiple classes extend one parent
```

### 3. Multilevel Inheritance
```java
class Animal { }
class Mammal extends Animal { }
class Dog extends Mammal { }  // Chain of inheritance
```

### 4. Multiple Inheritance (via Interfaces)
```java
interface Flyable { }
interface Swimmable { }
class Duck implements Flyable, Swimmable { }  // Supported through interfaces
```

---

## Key Concepts

### The `super` Keyword
- Refer to the parent class
- Call parent constructors, methods, and access parent fields

```java
class Parent {
    protected String name;
    public Parent(String name) {
        this.name = name;
    }
}

class Child extends Parent {
    private int age;
    
    public Child(String name, int age) {
        super(name);  // Calls parent constructor
        this.age = age;
    }
}
```

### Constructor Chaining
Constructors are called from parent to child automatically when using `super()`.

---

## Method Overriding

When a subclass provides a specific implementation of a method that is already defined in its parent class.

```java
class Animal {
    public void makeSound() {
        System.out.println("Some generic sound");
    }
}

class Dog extends Animal {
    @Override
    public void makeSound() {
        System.out.println("Woof! Woof!");
    }
}
```

**Rules for Overriding**:
- Method name must be the same
- Parameter list must be the same
- Return type must be the same or covariant
- Cannot reduce visibility level
- Must have `@Override` annotation (optional but recommended)

---

## Method Overloading vs Overriding

| Feature | Overloading | Overriding |
|---------|-------------|------------|
| Method Name | Same | Same |
| Parameters | Different | Same |
| Return Type | Can differ | Must be same or covariant |
| Purpose | Multiple forms in same class | Specialization in subclass |
| `super` call | N/A | Can use `@Override` |

---

## Important Keywords

### `final` Keyword with Inheritance
- `final class`: Cannot be extended
- `final method`: Cannot be overridden
- `final variable`: Value cannot be changed

```java
final class ImmutableClass { }  // Cannot extend
class Parent {
    final void display() { }  // Cannot override
}
```

### `abstract` Class
- Cannot be instantiated
- May contain abstract methods (must be overridden)
- Can contain concrete methods

```java
abstract class Shape {
    abstract double getArea();  // Must be implemented by subclasses
    
    void display() {  // Concrete method
        System.out.println("This is a shape");
    }
}
```

---

## Common Pitfalls

1. **Type Casting Issues**
   ```java
   Parent obj = new Child();  // Upcasting (allowed)
   Child child = (Child) obj;  // Downcasting (runtime check)
   ```

2. **Constructor Execution Order**
   ```java
   // Execution order: Parent constructor → Child constructor
   ```

3. **Overriding vs Overloading Confusion**
   - Ensure correct method signatures
   - Use `@Override` to verify overriding

---

## Summary

- **Inheritance** promotes code reuse and establishes relationships
- Use `extends` keyword to inherit from a class
- Use `implements` to implement interfaces (multiple supported)
- Override methods using `@Override` annotation
- Use `super` to access parent class members
- Abstract classes enable partial implementation
- Final classes/methods prevent extension/override

---

## Practice Example

```java
// Base class
class Vehicle {
    protected String brand;
    
    public Vehicle(String brand) {
        this.brand = brand;
    }
    
    public void start() {
        System.out.println(brand + " vehicle starting...");
    }
}

// Inherited class
class Car extends Vehicle {
    private int doors;
    
    public Car(String brand, int doors) {
        super(brand);
        this.doors = doors;
    }
    
    @Override
    public void start() {
        System.out.println("Car " + brand + " starting with " + doors + " doors");
    }
}

// Usage
public class Main {
    public static void main(String[] args) {
        Car myCar = new Car("Toyota", 4);
        myCar.start();  // Uses overridden method
    }
}
```

---

## Best Practices

1. Favor composition over inheritance when possible
2. Use interfaces to achieve multiple inheritance
3. Always use `@Override` annotation
4. Consider using `final` to prevent unwanted overrides
5. Follow the Liskov Substitution Principle (subtypes must be substitutable for base types)

---

*Source: Java Documentation, Oracle Java Tutorials, and standard OOP principles*